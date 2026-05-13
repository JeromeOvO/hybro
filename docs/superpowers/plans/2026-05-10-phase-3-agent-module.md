# Phase 3 Agent Module Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` if subagents are available, or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the Agent business module so `agent/` owns agent lifecycle, matching, discovery, health reads, direct-call decisions, and hub-agent sync through Common protocols.

**Architecture:** Add an `agent/` package with `AgentFacade` implementing `AgentRegistry`, `AgentMatcher`, `AgentManagement`, and `AgentRegistryWriter`. The facade depends only on Common DTOs/protocols, the domain `AgentRepository`, DAL protocols, and adapter protocols; legacy `services/` and `modules/` become migration wrappers that delegate through `bind_facade()` until Phase 9 cleanup.

**Tech Stack:** Python 3.11+, FastAPI, MongoDAL, VectorDAL, LLMProvider embedding API, AgentCardResolver adapter protocol, HubLivenessReader protocol, pytest, pytest-asyncio, AST import-boundary tests.

---

## Post-Review Hub Liveness Update

Final implementation note: the hub liveness fix did not keep the original split-protocol approach. `HubLivenessReader.is_hub_online(hub_id)` is now async and authoritative. `HubLivenessProbe` and `RelayHubLivenessProbe` were never needed and were removed. `validate_hub_liveness_reader()` now rejects sync implementations, `RelayHubLivenessReader.is_hub_online()` delegates directly to `RelayService.is_hub_alive()`, all `getattr(..., "is_hub_online_async", ...)` duck-typing was removed from the Agent facade and liveness service, and startup uses one adapter, one bind path, and one Common protocol.

## Scope

Include:
- Create or reconcile the `agent/` package on the Phase 3 branch.
- Implement `AgentFacade` as the concrete implementation for all four Agent protocols in `common/protocols/agent_protocols.py`.
- Implement `AgentMongoRepository` against `MongoDAL`, extending `AgentRepository` only where Phase 3 requires missing query/write operations.
- Move URL normalization, domain alias generation, agent matching score ranking, visibility filtering, and hub sync behavior into the Agent module.
- Add the C3 migration adapter in `services/agent_service.py`: all public methods delegate to a bound facade, and every delegated method raises `RuntimeError` before bind.
- Wire an `AgentDeps` sub-container in `container.py` and bind old singletons during `main.py` lifespan startup.
- Add unit, golden, endpoint compatibility, and import-boundary tests.

Exclude:
- Full API route extraction from `api/agent.py`; Phase 3 can keep routes as legacy adapters as long as endpoint responses stay identical.
- Removing `services/`, `modules/`, or legacy singleton imports globally; removal is Phase 9.
- HubRuntimeBridge extraction beyond using `HubLivenessReader` and `AgentRegistryWriter` contracts.
- Agent group extraction. `api/agent_group.py` and `models/agent_group.py` can stay legacy unless needed for tests.
- Capability issue service extraction. Matching can keep the existing exclusion behavior only through an injected protocol or explicit transitional adapter, not by importing `services.agent_capability_issue_service` from `agent/`.

## Current Repo Check

The prompt describes partial `agent/` scaffolding on `dev`, but the current branch was created from `main` and currently has no `agent/` directory and no `container.py`. Before implementation, reconcile this state:
- If `dev` contains the described scaffold, port or merge only the relevant `agent/` files into the Phase 3 branch.
- If `dev` is stale or unavailable, create the `agent/` files from this plan.
- Do not assume `agent/facade.py` or `agent/repository/mongo.py` exists until Task 0 verifies it.

IMPORTANT: Facade design change. The existing `dev` scaffold uses a delegation pattern where `AgentFacade` wraps injected `AgentMatcher`, `AgentManagement`, and `AgentRegistryWriter` protocol instances. Phase 3 replaces this entirely: the facade itself is the implementation. Remove the delegation constructor parameters `agent_matcher: AgentMatcher`, `agent_management: AgentManagement`, and `registry_writer: AgentRegistryWriter`. The facade owns matching logic, registration logic, health reads, direct-call checks, and hub sync logic directly, using only lower-level dependencies: `AgentRepository`, `VectorDAL`, `LLMProvider`, `AgentCardResolver`, and `HubLivenessReader`.

IMPORTANT: Do not port the full `dev:container.py` wholesale. `dev` has a large application container for later phases. On a branch from `main`, create a minimal `container.py` containing only `AgentDeps` and `create_agent_deps()` for Phase 3. Other module dependency factories are added in their respective phases.

Branch used for this plan: `phase-3-agent-module`, created from `main`.

## File Inventory

Create:
- `agent/__init__.py`: exports `AgentFacade` and `AgentMongoRepository`.
- `agent/facade.py`: concrete implementation of `AgentRegistry`, `AgentMatcher`, `AgentManagement`, and `AgentRegistryWriter`.
- `agent/matching.py`: pure scoring helpers migrated from `services/agent_matcher.py`.
- `agent/url_utils.py`: `normalize_agent_url()`, `is_local_agent_url()`, and well-known-path stripping.
- `agent/public_url.py`: domain alias generation currently in `services/domain_alias_service.py`, parameterized by repository availability checks.
- `agent/translators.py`: pure dict/DTO conversion helpers. This file must not import `models.*` or A2A SDK types.
- `agent/repository/__init__.py`: exports `AgentMongoRepository`.
- `agent/repository/mongo.py`: `AgentRepository` implementation using `MongoDAL`.
- `container.py`: minimal Phase 3 module sub-container with `AgentDeps` and `create_agent_deps()` only.
- `tests/test_agent_protocols.py`: runtime protocol conformance, exports, package list, and import-boundary tests.
- `tests/test_agent_repository.py`: repository tests against fake `MongoCollection`.
- `tests/test_agent_facade.py`: facade unit tests with fake repository/vector/card/hub dependencies.
- `tests/test_agent_golden.py`: golden behavior tests for register, delete, list, match, discovery, health, and `is_directly_callable`.

Delete if porting from `dev`:
- `agent/ports.py`: remove the temporary `AgentReader` legacy bridge. The Phase 3 facade uses `AgentRepository` directly and must not delegate reads to a legacy reader.

Modify:
- `common/protocols/repository_protocols.py`: extend `AgentRepository` only for missing Phase 3 queries/writes.
- `pyproject.toml`: add `agent` and `agent.repository` to `[tool.setuptools].packages`.
- `services/agent_service.py`: replace legacy business logic with C3 facade delegation and compatibility conversion.
- `services/agent_matcher.py`: convert to a thin adapter over `AgentFacade` or remove callers after `AgentSelectionService` is migrated.
- `services/agent_selection_service.py`: delegate matching to the bound facade while preserving `AgentSelectionResult`.
- `services/agent_liveness_service.py`: delegate hub liveness/offline behavior to bound Agent facade/repository rather than importing relay directly.
- `services/agent_health_service.py`: route health status writes through `AgentRepository.update_health()` or a bound facade helper.
- `services/relay_service.py`: route hub sync through `AgentRegistryWriter.sync_hub_agents()` once `AgentDeps` is available.
- `api/agent.py`: only adjust imports/wiring if needed; endpoint shapes should remain identical.
- `modules/AgentCenter.py`: keep as a legacy adapter that delegates to `services.agent_service`.
- `main.py`: build AgentDeps during lifespan startup and bind legacy adapters.

Reference-only:
- `services/agent_service.py`: legacy lifecycle behavior and response compatibility.
- `services/agent_matcher.py`: matching weights, I/O penalty, cutoff logic.
- `services/agent_selection_service.py`: legacy selection response shape.
- `services/agent_health_service.py`: health probe behavior and status update call sites.
- `services/agent_liveness_service.py`: current on-demand liveness semantics.
- `services/relay_service.py`: hub sync and Pinecone indexing behavior to move into `AgentRegistryWriter`.
- `common/protocols/agent_protocols.py`: target facade protocols.
- `common/protocols/repository_protocols.py`: repository contract.
- `common/protocols/dal_protocols.py`: `MongoDAL` and `VectorDAL`.
- `common/protocols/a2a_protocols.py`: `AgentCardResolver`.
- `common/protocols/llm_protocols.py`: `LLMProvider.embed()`.
- `common/protocols/hub_protocols.py`: async `HubLivenessReader` and `validate_hub_liveness_reader()`.

## Dependency Diagram

```text
api/agent.py
  -> services.agent_service.AgentService       legacy adapter, migration only
    -> agent.facade.AgentFacade                protocol implementation
      -> common.dto.*
      -> common.protocols.AgentRepository
      -> common.protocols.VectorDAL
      -> common.protocols.LLMProvider
      -> common.protocols.AgentCardResolver
      -> common.protocols.HubLivenessReader
      -> agent.repository.AgentMongoRepository
        -> common.protocols.MongoDAL

services.relay_service
  -> AgentRegistryWriter sync_hub_agents(), mark_hub_agents_offline()

execution/room/platform callers
  -> AgentRegistry, AgentMatcher, AgentManagement protocols only
```

Forbidden from `agent/**`:
- `services`
- `modules`
- `api`
- `database`
- `models`
- `main`
- `container`
- `a2a_adapter`
- `llm_gateway`
- `infrastructure`
- legacy `config`

Allowed in `agent/**`:
- stdlib
- `common.*`
- relative imports inside `agent`

Additional import-boundary detail:
- `agent/repository/mongo.py` may reference `common.protocols.MongoDAL` in its constructor signature.
- No `agent/**` file may import from `dal` at all, including concrete implementations such as `dal.mongo.client.MongoDALImpl`.
- The application shell in `container.py` owns concrete implementation construction.

## Interface Definitions

### AgentFacade Constructor

Use explicit dependency injection. Do not construct singletons inside `agent/`.

```python
from collections.abc import Callable
from datetime import datetime
from typing import Any

from common.observability import tracer as default_tracer
from common.protocols import (
    AgentCardResolver,
    AgentRepository,
    HubLivenessReader,
    LLMProvider,
    VectorDAL,
)

class AgentFacade:
    def __init__(
        self,
        *,
        repository: AgentRepository,
        vector: VectorDAL,
        llm_provider: LLMProvider,
        card_resolver: AgentCardResolver,
        hub_liveness: HubLivenessReader | None = None,
        agent_index: str = "a2a-agents",
        gateway_base_url: str | None = None,
        public_url_base_domain: str = "hybro.ai",
        public_url_protocol: str = "https",
        id_factory: Callable[[], str],
        now: Callable[[], datetime],
        tracer: Any | None = None,
    ) -> None: ...
```

Do not include the old delegation parameters from `dev`: `agent_reader`, `agent_matcher`, `agent_management`, or `registry_writer`. Keep tracing by using `tracer or default_tracer` from `common.observability`; importing observability from `common` is allowed by the module boundary.

Final hub liveness contract after review fixes:

```python
@runtime_checkable
class HubLivenessReader(Protocol):
    async def is_hub_online(self, hub_id: str) -> bool: ...
    async def get_hub_owner_id(self, hub_id: str) -> str | None: ...
```

`HubLivenessProbe` is not part of the final design. `validate_hub_liveness_reader()` must reject sync `is_hub_online()` implementations so sync readers cannot accidentally satisfy the runtime protocol and return truthy coroutine mismatches in async consumers.

Protocol methods implemented exactly:
- `get_agent(agent_id: str) -> AgentInfo | None`
- `get_agent_card(agent_id: str) -> AgentCardSnapshot | None`
- `get_agents_by_ids(agent_ids: list[str]) -> list[AgentInfo]`
- `is_agent_healthy(agent_id: str) -> bool`
- `is_directly_callable(agent_id: str) -> bool`
- `match_agents(query, limit=5, filter_ids=None, respect_visibility=True, requesting_user_id=None) -> list[AgentMatchResult]`
- `register_agent(url: str, provider_id: str, **kwargs) -> AgentInfo`
- `delete_agent(agent_id: str, provider_id: str) -> bool`
- `update_agent(agent_id: str, updates: dict) -> AgentInfo | None`
- `list_agents(provider_id: str) -> list[AgentInfo]`
- `list_public_agents(limit: int = 50) -> list[AgentInfo]`
- `sync_hub_agents(hub_id, owner_user_id, agents, prune_missing=True) -> list[SyncedHubAgent]`
- `mark_hub_agents_offline(hub_id: str) -> None`

Non-protocol compatibility helpers allowed on `AgentFacade`:
- `resolve_agent_card_from_url(url: str) -> AgentCardSnapshot | None`, used by the legacy `getAgentCardFromUrl` endpoint.
- `match_for_message(..., required_input_modes: list[str] | None = None, is_debate_mode: bool = False)`, used by legacy `AgentSelectionService` while protocol `match_agents()` remains unchanged.
- `update_health(agent_id: str, healthy: bool) -> None`, used by the health job if moving the job directly to `AgentRepository` would require too much churn.

### AgentRepository Additions

Start from the existing protocol:

```python
class AgentRepository(Protocol):
    async def get_by_id(self, agent_id: str) -> dict | None: ...
    async def get_by_ids(self, agent_ids: list[str]) -> list[dict]: ...
    async def get_by_provider(self, provider_id: str) -> list[dict]: ...
    async def get_public(self, limit: int = 50) -> list[dict]: ...
    async def upsert(self, agent_id: str, data: dict) -> None: ...
    async def delete(self, agent_id: str) -> bool: ...
    async def update_health(self, agent_id: str, healthy: bool) -> None: ...
    async def mark_hub_agents_offline(self, hub_id: str) -> int: ...
```

Verify completeness in Task 2. If missing, add these methods because the facade cannot own raw Mongo queries:

```python
async def find_by_normalized_url(
    self, normalized_url: str, provider_id: str | None = None
) -> dict | None: ...

async def list_visible(
    self,
    *,
    user_id: str | None = None,
    active_only: bool = False,
    agent_ids: list[str] | None = None,
    limit: int = 0,
) -> list[dict]: ...

async def update(self, agent_id: str, updates: dict) -> dict | None: ...

async def public_url_exists(self, subdomain: str, base_domain: str) -> bool: ...

async def upsert_hub_agent(
    self, hub_id: str, local_agent_id: str, data: dict
) -> str: ...

async def prune_missing_hub_agents(
    self, hub_id: str, active_agent_ids: list[str]
) -> int: ...

async def activate_agents(self, agent_ids: list[str]) -> int: ...

async def get_indexed_description_hash(self, agent_id: str) -> str | None: ...

async def set_indexed_description_hash(self, agent_id: str, desc_hash: str) -> None: ...
```

Keep repository inputs and outputs as dicts. The repository must not return `models.agent.Agent`.

### AgentDeps Sub-Container

Create minimal `container.py` content for Phase 3. Do not port the full `dev` app container unless the branch already contains it:

```python
from dataclasses import dataclass

from common.protocols import (
    AgentManagement,
    AgentMatcher,
    AgentRegistry,
    AgentRegistryWriter,
)

@dataclass(frozen=True)
class AgentDeps:
    agent_registry: AgentRegistry
    agent_matcher: AgentMatcher
    agent_management: AgentManagement
    agent_registry_writer: AgentRegistryWriter
```

Because one `AgentFacade` implements all four protocols, the initial assembly can bind all fields to the same instance.

## Implementation Order

Parallelization note: Tasks 2 and 3 are independent after Task 1 lands. If using `superpowers:subagent-driven-development`, split repository work and pure utility work into separate workers with disjoint write sets.

### Task 0: Branch, Baseline, and Scaffold Reconciliation

**Files:**
- Maybe create: `agent/**`
- Maybe create: `container.py`
- No behavior changes yet

- [ ] **Step 1: Verify branch starts from `main`**

```bash
git status --short --branch
git log --oneline --decorate -5
```

Expected: branch is `phase-3-agent-module` or another Phase 3 branch from `main`; worktree is clean except planned changes.

- [ ] **Step 2: Check whether the `dev` scaffold exists**

```bash
git ls-tree -r --name-only dev -- agent container.py
```

Expected: either the prompt-described scaffold exists, or the command reports no `agent/` files.

- [ ] **Step 3: If scaffold exists, inspect before porting**

```bash
git show dev:agent/facade.py | sed -n '1,220p'
git show dev:agent/repository/mongo.py | sed -n '1,220p'
```

Expected: confirm whether the scaffold delegates to legacy services. Do not keep any `agent/**` import from `services`, `modules`, `api`, `database`, `models`, `main`, or `container`.

- [ ] **Step 4: Remove delegation-only scaffold pieces**

If porting from `dev`:
- Replace `agent/facade.py` instead of extending the delegation design.
- Remove constructor parameters `agent_reader`, `agent_matcher`, `agent_management`, and `registry_writer`.
- Delete `agent/ports.py`; the `AgentReader` bridge is no longer part of Phase 3.
- Keep useful translator or tracing patterns only if they still satisfy the logic-owning facade design.

- [ ] **Step 5: Check referenced test-file availability**

```bash
for path in \
  tests/test_service_agent.py \
  tests/test_agent_matcher.py \
  tests/test_agent_selection_service_facade.py \
  tests/test_p2_modules_services.py
do
  test -f "$path" && printf "exists %s\n" "$path" || printf "missing %s\n" "$path"
done
```

Expected: record which files already exist. For missing files, create them in the task that first references them rather than silently dropping coverage.

- [ ] **Step 6: Run baseline tests for completed phases**

```bash
uv run python -m pytest tests/test_common_foundation.py tests/test_dal_protocols.py tests/test_adapter_protocols.py -q
```

Expected: PASS before Phase 3 changes.

- [ ] **Step 7: Commit scaffold reconciliation only**

```bash
git add agent pyproject.toml container.py tests
git commit -m "chore: prepare agent module scaffold"
```

Expected: commit only if files changed. Skip if there was nothing to port/create yet.

### Task 1: Add Failing Agent Protocol and Boundary Tests

**Files:**
- Create: `tests/test_agent_protocols.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add runtime protocol conformance test**

Assert:
- `AgentFacade(...)` is an `AgentRegistry`.
- `AgentFacade(...)` is an `AgentMatcher`.
- `AgentFacade(...)` is an `AgentManagement`.
- `AgentFacade(...)` is an `AgentRegistryWriter`.
- `AgentMongoRepository(mongo=fake_mongo)` is an `AgentRepository`.

- [ ] **Step 2: Add top-level export test**

Assert:
- `from agent import AgentFacade` works.
- `from agent.repository import AgentMongoRepository` works.
- `agent.__all__ == ["AgentFacade", "AgentMongoRepository"]` or the final explicit export set.

- [ ] **Step 3: Add package-list test**

Assert `pyproject.toml` includes:
- `agent`
- `agent.repository`

- [ ] **Step 4: Add import-boundary AST test**

Use the helper style from `tests/test_adapter_protocols.py`. Allowed roots:
- `__future__`
- stdlib roots from `sys.stdlib_module_names`
- `common`
- `agent`

Forbidden roots:
- `a2a_adapter`
- `api`
- `config`
- `container`
- `database`
- `infrastructure`
- `llm_gateway`
- `main`
- `models`
- `modules`
- `services`

- [ ] **Step 5: Run and verify failure**

```bash
uv run python -m pytest tests/test_agent_protocols.py -q
```

Expected before implementation: FAIL because `agent` package or concrete classes are missing.

### Task 2: Extend and Implement AgentRepository

**Files:**
- Modify: `common/protocols/repository_protocols.py`
- Create/modify: `agent/repository/mongo.py`
- Create/modify: `agent/repository/__init__.py`
- Create: `tests/test_agent_repository.py`

- [ ] **Step 1: Write repository contract tests**

Cover:
- `get_by_id()` queries `{"agent_id": agent_id}`.
- `get_by_ids()` returns only matching ids and preserves repository output as dicts.
- `get_by_provider()` queries `{"provider_id": provider_id}`.
- `get_public()` returns public or missing `is_public`, with limit.
- `list_visible(user_id=None, active_only=True)` returns public active agents only.
- `list_visible(user_id="u1", active_only=True)` returns public active agents plus private active agents owned by `u1`.
- `find_by_normalized_url()` checks `normalized_url` and legacy documents missing `normalized_url`.
- `public_url_exists()` checks the current domain alias collision behavior.
- `update()` returns the updated document or `None`.
- `delete()` deletes by `agent_id`.
- `update_health()` sets `agent_status` to `"active"` or `"inactive"`.
- `upsert_hub_agent()` upserts by `(hub_id, local_agent_id)` and returns stable `agent_id`.
- `prune_missing_hub_agents()` marks missing hub agents inactive.
- `mark_hub_agents_offline()` marks all hub agents inactive.

- [ ] **Step 2: Extend `AgentRepository` protocol only as needed**

Use the additions listed in "AgentRepository Additions". Keep the protocol domain-scoped; do not expose generic `find(query)` or raw collection access.

- [ ] **Step 3: Implement `AgentMongoRepository`**

Implementation notes:
- Constructor accepts `mongo: MongoDAL` and optional `collection_name: str = "agents"`.
- Store `self._agents = mongo.collection(collection_name)`.
- Use `MongoCollection.find_one`, `find`, `update_one`, `update_many`, `delete_one`, and `count`.
- Do not import `database.mongodb`, `pymongo`, or `models.agent`.
- For `find_by_normalized_url()`, mirror legacy compatibility:
  - First query exact `normalized_url`.
  - Then scan legacy docs where `normalized_url` is missing and compare `normalize_agent_url(doc["agent_card"]["url"])`.
  - If `provider_id` is supplied, include it in both checks.
- For `upsert_hub_agent()`, prefer stable `agent_id`:
  - Read existing by `(hub_id, local_agent_id)`.
  - If exists, update fields and return existing `agent_id`.
  - If missing, upsert with provided `agent_id`; then read back and return stored `agent_id`.

- [ ] **Step 4: Run repository tests**

```bash
uv run python -m pytest tests/test_agent_repository.py -q
```

Expected: PASS.

- [ ] **Step 5: Run protocol tests subset**

```bash
uv run python -m pytest tests/test_agent_protocols.py -k "repository or package" -q
```

Expected: repository conformance and package tests PASS; facade tests may still fail.

### Task 3: Add Pure Agent Utilities

**Files:**
- Create: `agent/url_utils.py`
- Create: `agent/public_url.py`
- Create: `agent/matching.py`
- Create: `agent/translators.py`
- Create: `tests/test_agent_facade.py` utility sections

- [ ] **Step 1: Port URL normalization tests**

Cover:
- `http://127.0.0.1:80/.well-known/agent.json` normalizes to `http://localhost`.
- `https://EXAMPLE.com:443/path/` normalizes to `https://example.com/path`.
- `/.well-known/agent-card.json` and `/.well-known/agent.json` are stripped.
- Query strings are preserved.
- `is_local_agent_url()` returns true for `localhost`, `127.0.0.1`, `::1`, and `0.0.0.0`.

- [ ] **Step 2: Implement `agent/url_utils.py`**

Move the logic from `services/agent_service.py`. Keep it pure and SDK-free.

- [ ] **Step 3: Port public URL generation tests**

Cover:
- Preferred subdomain is normalized and used when available.
- Reserved subdomains are rejected.
- Agent-name fallback removes filler words and invalid DNS characters.
- Hash suffix is used when base subdomain is taken.
- UUID fallback is used when both preferred/base/hash are taken.

- [ ] **Step 4: Implement `agent/public_url.py`**

Use a small class or pure function that receives:
- `exists: Callable[[str], Awaitable[bool]]` or repository `public_url_exists()`
- `base_domain`
- `protocol`
- `id_factory` for fallback suffixes

Subdomain availability must be checked across all providers, because public URLs are global aliases. That check belongs behind `AgentRepository.public_url_exists(subdomain, base_domain)`. Do not import `services.domain_alias_service` into `agent/`.

- [ ] **Step 5: Port matching helper tests**

Cover from `services/agent_matcher.py`:
- File-capable input modes: `"file"`, `"*/*"`, `image/*`, `audio/*`, `video/*`, PDF, zip, tar, gzip.
- Capability score is `1.0` when no attachments are required.
- Capability score is `0.0` when attachments are required and agent supports only text.
- Final score is `0.85 * vector_score + 0.15 * capability_score` by default.
- Debate cutoff returns 3 to 5 above threshold or top 2 fallback.
- Non-debate cutoff returns one clear winner, otherwise up to 3 above quality threshold.

- [ ] **Step 6: Implement `agent/matching.py`**

Keep environment-backed defaults compatible with the legacy constants:
- `MATCH_VECTOR_WEIGHT`, default `0.85`
- `MATCH_CAPABILITY_WEIGHT`, default `0.15`
- `MATCH_DEBATE_THRESHOLD`, default `0.3`
- `MATCH_GAP_THRESHOLD`, default `0.15`
- `MATCH_QUALITY_THRESHOLD`, default `0.4`

Use dict/DTO inputs. Do not import `models.agent.Agent`.

- [ ] **Step 7: Add translator tests**

Cover:
- Mongo dict to `AgentInfo`.
- Mongo dict to `AgentCardSnapshot`.
- `AgentCardSnapshot` plus metadata to Mongo agent document for registration.
- Hub descriptor to Mongo update dict.
- DTO ordering by vector result order.

- [ ] **Step 8: Implement `agent/translators.py`**

Rules:
- Read card fields from `doc["agent_card"]` first.
- `AgentInfo.status` maps from `agent_status`, defaulting to `"active"`.
- `AgentInfo.url` maps to real `agent_card.url`; API masking remains in legacy adapter.
- Preserve `raw_card` in `AgentCardSnapshot`.
- Do not instantiate legacy Pydantic `models.agent.Agent`.

- [ ] **Step 9: Run utility tests**

```bash
uv run python -m pytest tests/test_agent_facade.py -k "url or public_url or matching or translator" -q
```

Expected: PASS.

### Task 4: Implement AgentFacade Registry Reads, Health Reads, and Direct-Call Semantics

**Files:**
- Create/modify: `agent/facade.py`
- Modify: `tests/test_agent_facade.py`

- [ ] **Step 1: Write failing registry tests**

Cover:
- `get_agent()` returns `AgentInfo` for existing repository doc.
- `get_agent()` returns `None` for missing doc.
- `get_agent_card()` returns `AgentCardSnapshot` from stored card.
- `get_agents_by_ids()` preserves caller id order even if repository returns unordered docs.
- `is_agent_healthy()` returns true only when `agent_status == "active"`.
- `is_agent_healthy()` returns false for missing agent.
- `is_directly_callable()` returns true for cloud active agents.
- `is_directly_callable()` returns false for inactive agents.
- `is_directly_callable()` returns false for `source == "hub"` when `HubLivenessReader` says offline.
- `is_directly_callable()` returns true for hub-source agents when hub is online and the current compatibility behavior allows direct fallback.

Note: If product policy says hub-source agents must never be called directly, update this last expected result before implementation. The prompt requires only "Returns False for hub-source agents when hub is offline."

- [ ] **Step 2: Implement registry methods**

Use repository reads only. Hydrate `is_hub_online` using `await hub_liveness.is_hub_online(hub_id)` when `hub_id` exists.

- [ ] **Step 3: Implement `is_directly_callable()`**

Algorithm:
1. Fetch agent.
2. Return false if missing or status is not `"active"`.
3. If `source != "hub"` and no `hub_id`, return true.
4. If hub-backed and `hub_liveness` is missing, return false fail-closed.
5. Return the hub liveness result.

`HubLivenessReader.is_hub_online()` is async in the final implementation. Inside async facade methods, call `await self._hub_liveness.is_hub_online(hub_id)`. Do not add `HubLivenessProbe`, `is_hub_online_async`, or duck-typed fallback paths.

- [ ] **Step 4: Run registry tests**

```bash
uv run python -m pytest tests/test_agent_facade.py -k "registry or health or directly_callable" -q
```

Expected: PASS.

### Task 5: Implement Agent Lifecycle Management

**Files:**
- Modify: `agent/facade.py`
- Modify: `agent/translators.py`
- Modify: `tests/test_agent_facade.py`

- [ ] **Step 1: Write failing registration tests**

Cover:
- Missing URL raises `ValueError` or a domain-specific Common error if one exists.
- `register_agent()` calls `AgentCardResolver.resolve_card(url)`.
- Resolver returning `None` fails without repository write.
- Duplicate normalized URL returns a duplicate error compatible with legacy adapter behavior.
- Local URLs do not use global `normalized_url` when registering hub-sourced local agents.
- Successful registration writes a complete agent document:
  - generated `agent_id`
  - `provider_id`
  - `agent_card`
  - `normalized_url`
  - `public_url`
  - `agent_status == "active"`
  - `is_public` default true unless overridden
  - rate limit fields if provided
- Successful registration upserts vector index using description embedding.
- Rollback deletes Mongo doc if vector upsert fails after repository write.

- [ ] **Step 2: Implement `resolve_agent_card_from_url()`**

This is a non-protocol helper for the legacy public card endpoint. It should call the injected `AgentCardResolver` and return `AgentCardSnapshot | None`.

- [ ] **Step 3: Implement `register_agent()`**

Algorithm:
1. Normalize URL.
2. Resolve card using `card_resolver.resolve_card(url)`.
3. Build normalized URL from the resolved card URL when present; otherwise from request URL.
4. Check duplicates through `repository.find_by_normalized_url(normalized_url, provider_id=None)` for cloud registrations.
5. Generate `agent_id`.
6. Generate `public_url` through `agent/public_url.py`.
7. Build repository dict via translator.
8. `repository.upsert(agent_id, doc)`.
9. Embed description with `llm_provider.embed(description)`.
10. `vector.upsert(agent_index, [VectorRecord(...)])`.
11. Return `AgentInfo`.

- [ ] **Step 4: Write failing delete tests**

Cover:
- Missing agent returns false.
- Non-owner delete returns false or raises permission error, then legacy adapter maps to 403.
- Owner delete removes repository doc and vector record.
- Vector delete failure after Mongo delete is logged and returns false, matching current best-effort behavior if required by golden tests.

- [ ] **Step 5: Implement `delete_agent()`**

Require `provider_id` ownership check in facade. Do not leave ownership only in API routes.

- [ ] **Step 6: Write failing update tests**

Cover:
- Missing agent returns `None`.
- Updating rate limits validates positive integer or `None`.
- Updating `is_public` and `agent_status` persists.
- Updating card description re-embeds and upserts vector.
- Updating card never overwrites Hybro-managed `iconUrl` unless explicitly allowed by existing behavior.

- [ ] **Step 7: Implement `update_agent()`**

Keep accepted update keys narrow:
- `agent_status`
- `is_public`
- `rate_limit_per_user_per_hour`
- `rate_limit_system_per_hour`
- `agent_card` or `agent_card.*` fields needed by existing API

Reject unknown write keys at the facade boundary.

- [ ] **Step 8: Write failing list tests**

Cover:
- `list_agents(provider_id)` returns all owned agents.
- `list_public_agents(limit)` returns only public agents.
- Hub liveness fields are hydrated on listed hub agents.

- [ ] **Step 9: Implement list methods**

Use repository methods and translators. Preserve ordering returned by repository unless an endpoint golden test requires sort order.

- [ ] **Step 10: Run lifecycle tests**

```bash
uv run python -m pytest tests/test_agent_facade.py -k "register or delete or update or list" -q
```

Expected: PASS.

### Task 6: Implement Matching and Discovery

**Files:**
- Modify: `agent/facade.py`
- Modify: `agent/matching.py`
- Modify: `tests/test_agent_facade.py`
- Create/modify: `tests/test_agent_golden.py`

- [ ] **Step 1: Confirm VectorDAL signature before coding**

```bash
sed -n '60,78p' common/protocols/dal_protocols.py
```

Expected signature:

```python
async def search(
    self, index: str, vector: list[float], top_k: int, filter: dict | None = None
) -> list[VectorSearchResult]: ...
```

If the signature differs on the implementation branch, update the facade call sites and tests to match the protocol, not an assumed Pinecone API.

- [ ] **Step 2: Write failing protocol matching tests**

Cover:
- Empty query returns empty list or raises the same error legacy adapter expects.
- `respect_visibility=True` with `requesting_user_id=None` matches only public active agents.
- `respect_visibility=True` with `requesting_user_id="u1"` matches public active agents plus private active agents owned by `u1`.
- `respect_visibility=False` matches public active discovery candidates only, not another user's private agents.
- `filter_ids` intersects with visibility result.
- Matching preserves vector score order before score-ranker cutoff.
- Result includes `AgentMatchResult.agent` with hydrated `AgentInfo`.

- [ ] **Step 3: Implement visibility candidate resolution**

Algorithm:
1. Ask repository for visible active candidate docs.
2. Intersect with `filter_ids` if provided.
3. If no candidates, return empty without embedding/vector calls.
4. Build vector filter with `{"agent_id": {"$in": candidate_ids}}`.

Use the existing Pinecone metadata shape: `{"type": "a2a_agent", "agent_id": agent_id}`.

- [ ] **Step 4: Implement `match_agents()`**

Algorithm:
1. Embed query through `llm_provider.embed(query)`.
2. Search `VectorDAL.search(agent_index, embedding, top_k=max(limit * 3, 15), filter=...)`.
3. Fetch matched docs by ids.
4. Apply active/visibility filter again after vector search for defense in depth.
5. Compute final scores using `agent/matching.py`.
6. Select final agents using non-debate cutoff, then slice to `limit`.
7. Return `AgentMatchResult` list.

- [ ] **Step 5: Implement legacy matching compatibility helper**

Add `match_for_message()` if needed by `services/agent_selection_service.py`:
- Accept `required_input_modes`.
- Accept `is_debate_mode`.
- Return enough score breakdown for legacy `AgentSelection.reason`.

- [ ] **Step 6: Add golden discovery tests**

Golden cases:
- Public discovery excludes private agents when unauthenticated.
- Authenticated matching includes caller-owned private agents.
- Inactive agents do not appear.
- `filter_ids=[]` returns empty and does not fall back to unrestricted search.

- [ ] **Step 7: Run matching tests**

```bash
uv run python -m pytest tests/test_agent_facade.py tests/test_agent_golden.py -k "match or discovery" -q
```

Expected: PASS.

### Task 7: Implement Hub Agent Sync Writer

**Files:**
- Modify: `agent/facade.py`
- Modify: `agent/translators.py`
- Modify: `tests/test_agent_facade.py`
- Modify: `tests/test_agent_golden.py`

- [ ] **Step 1: Write failing hub sync tests**

Cover:
- Invalid descriptors are skipped without aborting the full sync.
- Existing cloud agent with same normalized URL and same owner is enriched with hub metadata.
- New hub agent is upserted by `(hub_id, local_agent_id)`.
- Local hub agent URLs do not use global `normalized_url`.
- `is_public` defaults false for hub-sourced agents.
- `public_url` is set to gateway proxy URL when `gateway_base_url` exists.
- Missing synced ids are marked inactive when `prune_missing=True`.
- Prune is skipped when the hub submitted descriptors but all were invalid.
- Description hash prevents unnecessary re-embedding.
- Changed description re-embeds and upserts vector.
- Hub-online activation sets synced agents active.
- `mark_hub_agents_offline()` delegates to repository and does not import relay.

- [ ] **Step 2: Implement descriptor validation without A2A SDK**

Because `agent/` cannot import A2A SDK types, validate only the fields required for persistence:
- descriptor has `agent_id`
- card has `url`, `name`, and `description` when available
- card raw dict is preserved

Let adapter-layer card validation remain in the adapter package.

- [ ] **Step 3: Implement `sync_hub_agents()`**

Port behavior from `services/relay_service.py`:
1. For each descriptor, normalize URL.
2. If local URL, store `normalized_url = None`.
3. Check owner-scoped existing agent by normalized URL.
4. If existing, update source/hub/local/status/card fields.
5. Otherwise upsert by `(hub_id, local_agent_id)`.
6. Set proxy `public_url` from `gateway_base_url`.
7. Hash description and index only when changed.
8. Prune missing agents when requested.
9. If `await hub_liveness.is_hub_online(hub_id)`, activate synced ids.
10. Return `SyncedHubAgent` DTOs.

- [ ] **Step 4: Run hub sync tests**

```bash
uv run python -m pytest tests/test_agent_facade.py tests/test_agent_golden.py -k "hub or sync or offline" -q
```

Expected: PASS.

### Task 8: Add C3 Migration Adapters

**Files:**
- Modify: `services/agent_service.py`
- Modify: `services/agent_matcher.py`
- Modify: `services/agent_selection_service.py`
- Modify: `services/agent_liveness_service.py`
- Modify: `services/agent_health_service.py`
- Modify: `modules/AgentCenter.py` only if its calls no longer line up
- Modify: `tests/test_service_agent.py`
- Modify: `tests/test_agent_matcher.py`
- Modify: `tests/test_agent_selection_service_facade.py`
- Modify: `tests/test_p2_modules_services.py`

- [ ] **Step 1: Create any missing migration test files**

Use the Task 0 test-file check. If any referenced files are missing on the implementation branch, create them here with focused coverage for the adapter being changed. Do not skip coverage because a file did not exist on `main`.

- [ ] **Step 2: Write fail-fast binding tests for `AgentService`**

Cover:
- New `AgentService()` has `_bound is False`.
- Calling any delegated method before `bind_facade()` raises `RuntimeError("AgentService.bind_facade() not called")`.
- After bind, legacy methods call the facade.

- [ ] **Step 3: Replace `services/agent_service.py` business logic with adapter**

Target shape:

```python
class AgentService:
    def __init__(self) -> None:
        self._facade = None
        self._bound = False

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "AgentService.bind_facade() not called - startup incomplete"
            )
        return self._facade
```

Legacy method mapping:
- `get_agent_card_from_url(request)` -> `facade.resolve_agent_card_from_url(request.agent_url)` -> `AgentCenterResponse`.
- `register_agent(request)` -> `facade.register_agent(request.agent_url, request.provider_id, **kwargs)` -> `AgentCenterResponse`.
- `update_agent(request)` -> `facade.update_agent(request.agent_id, updates)` -> `AgentCenterResponse`.
- `remove_agent(request)` -> `facade.delete_agent(request.agent_id, provider_id)`; if provider is missing in the legacy request, fetch first and use owner.
- `query_agent_by_agent_id(request)` -> `facade.get_agent(request.agent_id)` plus visibility check for `request.user_id`.
- `get_agents_by_provider_id(request)` -> `facade.list_agents(provider_id)`.
- `get_all_agents(request)` -> repository-visible list through a non-protocol helper or `list_public_agents` plus caller-owned private list.
- `get_all_active_agents(request)` -> same, active only.
- `query_similar_agents(request)` -> `facade.match_agents(..., respect_visibility=True, requesting_user_id=request.user_id)`.
- `get_agent_url_by_agent_id(request)` -> `facade.get_agent_card(agent_id).url`.
- `get_agent_by_agent_id(agent_id)` -> compatibility helper returning legacy model or DTO accepted by callers.

Keep `normalize_agent_url()` and `is_local_agent_url()` as re-exporting wrappers that import from `agent.url_utils`. This preserves imports in `services/relay_service.py` and `services/gateway_service.py` during migration.

- [ ] **Step 4: Add legacy conversion helpers in `services/agent_service.py`**

This is the only place allowed to convert new DTOs to `models.agent.Agent` for old callers. `agent/` must not import `models`.

Conversion rules:
- Convert `AgentCardSnapshot.raw_card` to `a2a.types.AgentCard` if old callers require the SDK model.
- Keep `provider_name`, `is_hub_online`, `source`, `hub_id`, `public_url`, status, and rate limits.
- Masking behavior remains in `_mask_sensitive_information()`.

- [ ] **Step 5: Migrate `services/agent_matcher.py`**

Options:
- Preferred: make `AgentMatcher` a compatibility adapter that receives/binds `AgentFacade` and calls `match_for_message()`.
- Acceptable transitional path: keep dataclasses `MatchedAgent` and `MatchResult`, but no Pinecone, DB, or scoring logic remains in `services/agent_matcher.py`.

Before bind, raise `RuntimeError` with the same fail-fast pattern.

- [ ] **Step 6: Migrate `services/agent_selection_service.py`**

Keep `RoutingStrategy`, `AgentSelection`, and `AgentSelectionResult`. Delegate selection to the bound facade or to the migrated `services.agent_matcher.AgentMatcher`.

- [ ] **Step 7: Migrate health/liveness writes**

`services/agent_health_service.py` can keep HTTP probing, backoff, and job scheduling, but status persistence must route through:
- `AgentFacade.update_health()` compatibility helper, or
- `AgentRepository.update_health()` bound by container.

`services/agent_liveness_service.py` should:
- For cloud agents: keep HTTP probe behavior in health service.
- For hub agents: call `AgentRegistryWriter.mark_hub_agents_offline()` and `await HubLivenessReader.is_hub_online()` through bound dependencies; do not import `services.relay_service` inside liveness code.
- Use the single bound `HubLivenessReader` adapter. Do not introduce `HubLivenessProbe`, `RelayHubLivenessProbe`, or `getattr(..., "is_hub_online_async", ...)` duck-typing.

- [ ] **Step 8: Run legacy adapter tests**

```bash
uv run python -m pytest tests/test_service_agent.py tests/test_agent_matcher.py tests/test_agent_selection_service_facade.py tests/test_p2_modules_services.py -q
```

Expected: PASS after updating tests for bind behavior.

### Task 9: Wire AgentDeps in Container and Startup

**Files:**
- Create/modify: `container.py`
- Modify: `main.py`
- Modify: `services/relay_service.py`
- Modify: tests that patch startup or relay dependencies

- [ ] **Step 1: Add container assembly tests**

Create tests that instantiate the container with fakes and assert:
- `AgentDeps.agent_registry` is an `AgentRegistry`.
- `AgentDeps.agent_matcher` is an `AgentMatcher`.
- `AgentDeps.agent_management` is an `AgentManagement`.
- `AgentDeps.agent_registry_writer` is an `AgentRegistryWriter`.
- `services.agent_service.agent_service.bind_facade()` is called during startup wiring.

- [ ] **Step 2: Implement minimal `container.py` AgentDeps assembly**

On branches that do not already contain `container.py`, create only the Phase 3 surface:
- `AgentDeps`
- `create_agent_deps()`

Do not copy `dev:container.py` in full. The full application container grows incrementally as later business modules are extracted.

Minimal target:

```python
def create_agent_deps(
    *,
    mongo: MongoDAL,
    vector: VectorDAL,
    llm_provider: LLMProvider,
    card_resolver: AgentCardResolver,
    hub_liveness: HubLivenessReader | None,
    gateway_base_url: str | None,
) -> AgentDeps:
    repository = AgentMongoRepository(mongo=mongo)
    facade = AgentFacade(
        repository=repository,
        vector=vector,
        llm_provider=llm_provider,
        card_resolver=card_resolver,
        hub_liveness=hub_liveness,
        gateway_base_url=gateway_base_url,
        id_factory=lambda: uuid4().hex,
        now=utcnow,
    )
    return AgentDeps(
        agent_registry=facade,
        agent_matcher=facade,
        agent_management=facade,
        agent_registry_writer=facade,
    )
```

If a broader app container already exists on the implementation branch because another refactor has already merged it, extend that file in place instead of creating a parallel container.

- [ ] **Step 3: Instantiate adapters during lifespan startup**

In `main.py`, after Mongo/Pinecone/adapter services are ready:
- Build `MongoDALImpl(database=mongodb.db)`.
- Build `VectorDALImpl(client=...)` or use existing Phase 1 DAL construction.
- Build/use `LLMGatewayImpl` as `LLMProvider`.
- Build/use `AgentCardResolverImpl` as `AgentCardResolver`.
- Use `RelayHubLivenessReader` as the single `HubLivenessReader` once relay is available; before relay init, allow `hub_liveness=None` and bind it after relay construction if needed. `RelayHubLivenessReader.is_hub_online()` delegates directly to `RelayService.is_hub_alive()`.
- Bind `agent_service.bind_facade(facade)`.
- Bind `services.agent_matcher`, `services.agent_selection_service`, health service, and liveness service as needed.
- Pass `AgentRegistryWriter` into relay sync path.

- [ ] **Step 4: Update `services/relay_service.py` sync path**

During transition:
- `RelayService` receives `agent_registry_writer: AgentRegistryWriter | None`.
- If not bound, fail fast for sync operations that require agent writes.
- Replace direct Mongo agent sync writes with `agent_registry_writer.sync_hub_agents(...)`.
- Keep relay connection, stream, heartbeat, and offline queue logic in relay service.

- [ ] **Step 5: Run startup-related tests**

```bash
uv run python -m pytest tests/test_multi_worker_safety.py tests/test_api_relay.py tests/test_heartbeat_fixes.py -q
```

Expected: PASS.

### Task 10: Golden Tests and Endpoint Compatibility

**Files:**
- Create/modify: `tests/test_agent_golden.py`
- Modify: `tests/test_api_agent.py`
- Modify: `tests/test_api_discovery.py`
- Modify: `tests/test_api_gateway.py` if direct-call behavior is surfaced there
- Modify: `tests/test_flow_contracts.py`

- [ ] **Step 1: Add golden register test**

Fixture:
- Fake card resolver returns a card for `https://agent.example`.
- Fake repository initially empty.
- Fake embedding provider returns `[0.1, 0.2, 0.3]`.

Assert:
- endpoint/legacy adapter response status, success flag, provider id, public URL, and masked `agent_card.url` match current behavior.
- repository doc and vector record are written.

- [ ] **Step 2: Add golden delete test**

Assert:
- owner can delete.
- non-owner receives existing 403 behavior through API route.
- vector delete is invoked.

- [ ] **Step 3: Add golden list test**

Assert:
- owner listing includes private and public owned agents.
- public listing excludes private agents.
- API response masks real `agent_card.url`.

- [ ] **Step 4: Add golden match test**

Assert:
- vector score ranking and I/O penalty preserve legacy `services/agent_matcher.py` behavior.
- clear winner returns one agent.
- close scores return up to three.

- [ ] **Step 5: Add golden discovery test**

Assert:
- unauthenticated discovery does not reveal private agents.
- endpoint response shape matches `api/discovery.py` expectations if discovery is migrated to `AgentMatcher`.

- [ ] **Step 6: Add golden health test**

Assert:
- `is_agent_healthy()` returns true for active and false for inactive/missing.
- health job writes active/inactive through the repository/facade binding.

- [ ] **Step 7: Add golden `is_directly_callable` test**

Assert:
- cloud active agent returns true.
- inactive cloud agent returns false.
- hub active agent with offline hub returns false.
- missing hub liveness dependency fails closed.

- [ ] **Step 8: Run endpoint compatibility tests**

```bash
uv run python -m pytest tests/test_api_agent.py tests/test_api_discovery.py tests/test_flow_contracts.py -q
```

Expected: PASS with endpoint response bodies unchanged except for intentional fail-fast startup behavior in unit tests.

### Task 11: Final Import Boundary and Full Gate

**Files:**
- Modify: `tests/test_agent_protocols.py`
- Maybe modify: `docs/MODULAR_DECOUPLING_DESIGN.md` only if documenting actual Phase 3 deviations

- [ ] **Step 1: Run Agent module tests**

```bash
uv run python -m pytest tests/test_agent_protocols.py tests/test_agent_repository.py tests/test_agent_facade.py tests/test_agent_golden.py -q
```

Expected: PASS.

- [ ] **Step 2: Run legacy compatibility tests**

```bash
uv run python -m pytest tests/test_service_agent.py tests/test_agent_matcher.py tests/test_agent_selection_service_facade.py tests/test_api_agent.py tests/test_api_discovery.py tests/test_api_relay.py -q
```

Expected: PASS.

- [ ] **Step 3: Run completed phase tests**

```bash
uv run python -m pytest tests/test_common_foundation.py tests/test_dal_protocols.py tests/test_dal_unit.py tests/test_adapter_protocols.py tests/test_adapter_unit.py -q
```

Expected: PASS.

- [ ] **Step 4: Run import-boundary tests**

```bash
uv run python -m pytest tests/test_agent_protocols.py -k import_boundary -q
```

Expected: PASS and no forbidden imports from `agent/**`.

- [ ] **Step 5: Run broad regression suite if time allows**

```bash
uv run python -m pytest -q
```

Expected: PASS. If too slow, record the targeted commands above and any skipped broad-suite reason.

- [ ] **Step 6: Commit Phase 3**

```bash
git status --short
git add agent common/protocols/repository_protocols.py pyproject.toml container.py main.py services tests
git commit -m "feat: extract agent module facade"
```

Expected: one focused Phase 3 implementation commit, or several commits matching task boundaries if using subagents.

## Migration Adapter Wiring

The C3 pattern is mandatory for `services/agent_service.py`:
- No import-time construction of business dependencies.
- No fallback to legacy business logic before bind.
- Before bind, raise `RuntimeError`.
- After bind, all public methods delegate to the new facade.

Recommended binding order during startup:
1. Connect Mongo and initialize DAL.
2. Initialize adapter implementations (`AgentCardResolver`, `LLMProvider`).
3. Build `AgentDeps`.
4. Bind `services.agent_service.agent_service`.
5. Bind `services.agent_matcher` and `services.agent_selection_service` compatibility adapters.
6. Bind health/liveness compatibility services.
7. Initialize relay service and provide `AgentRegistryWriter` for hub sync.
8. Bind `RelayHubLivenessReader` as the single async `HubLivenessReader` before serving traffic.

Avoid circular imports:
- `container.py` can import concrete implementations.
- `main.py` can import `container.py`.
- `agent/**` must never import `container.py` or `main.py`.
- Legacy `services/**` may import `agent` during migration because they are wrappers, but `agent` must not import `services`.

## Test Plan

Unit tests:
- `tests/test_agent_repository.py`: Mongo repository query/update behavior against fakes.
- `tests/test_agent_facade.py`: facade behavior with fake repository, vector, LLM, card resolver, and hub liveness.
- `tests/test_agent_protocols.py`: runtime protocol conformance, exports, packaging, import boundaries.

Golden integration tests:
- `tests/test_agent_golden.py`: register, delete, list, match, discovery, health, and `is_directly_callable` behavior.
- Existing API tests: `tests/test_api_agent.py`, `tests/test_api_discovery.py`, `tests/test_flow_contracts.py`.
- Existing hub tests: `tests/test_api_relay.py`, `tests/test_heartbeat_fixes.py`.

Migration adapter tests:
- `tests/test_service_agent.py`: fail-fast before bind and exact response compatibility after bind.
- `tests/test_agent_matcher.py`: legacy matcher dataclasses and score breakdown still work through the facade.
- `tests/test_agent_selection_service_facade.py`: legacy selection output remains stable.
- `tests/test_p2_modules_services.py`: health service and module wrappers remain compatible.

Import boundary tests:
- `agent/**` imports only stdlib, `common`, and `agent`.
- `agent/**` does not import `services`, `modules`, `api`, `database`, `models`, `main`, `container`, `a2a_adapter`, or `llm_gateway`.
- Existing adapter and DAL boundary tests continue to pass.

Verification commands:

```bash
uv run python -m pytest tests/test_agent_protocols.py tests/test_agent_repository.py tests/test_agent_facade.py tests/test_agent_golden.py -q
uv run python -m pytest tests/test_service_agent.py tests/test_agent_matcher.py tests/test_agent_selection_service_facade.py -q
uv run python -m pytest tests/test_api_agent.py tests/test_api_discovery.py tests/test_api_relay.py tests/test_flow_contracts.py -q
uv run python -m pytest tests/test_common_foundation.py tests/test_dal_protocols.py tests/test_dal_unit.py tests/test_adapter_protocols.py tests/test_adapter_unit.py -q
```

## Gate Criteria Checklist

- [ ] `agent/` package exists and is listed in `pyproject.toml`.
- [ ] `AgentFacade` satisfies `AgentRegistry`, `AgentMatcher`, `AgentManagement`, and `AgentRegistryWriter` at runtime.
- [ ] `AgentMongoRepository` satisfies `AgentRepository` at runtime.
- [ ] `agent/**` import-boundary test passes.
- [ ] No `agent/**` imports from `services`, `modules`, `api`, `database`, `models`, `main`, `container`, `a2a_adapter`, or `llm_gateway`.
- [ ] `services/agent_service.py` uses `bind_facade()` and raises `RuntimeError` before bind.
- [ ] Agent registration resolves cards through `AgentCardResolver`, stores normalized URL/public URL, and indexes vectors through `VectorDAL`.
- [ ] Agent deletion enforces ownership and deletes vector records.
- [ ] Agent listing respects visibility and masks sensitive URLs through the legacy adapter/API layer.
- [ ] Matching uses `LLMProvider.embed()` plus `VectorDAL.search()` and internal Agent-module score ranking.
- [ ] Discovery does not leak private agents.
- [ ] `is_agent_healthy()` reads `agent_status == "active"`.
- [ ] Health job status writes route through repository/facade binding.
- [ ] `is_directly_callable()` fails closed for offline hub agents.
- [ ] Hub sync uses `AgentRegistryWriter` and no longer writes agent documents directly from relay service.
- [ ] Golden tests for register, delete, list, match, discovery, health, and `is_directly_callable` pass.
- [ ] Existing API response compatibility tests pass.
- [ ] Phase 0, Phase 1, and Phase 2 tests still pass.

## Risk Assessment

### Risk: Current branch lacks prompt-described scaffold

Impact: Implementation may duplicate or conflict with `dev` scaffold if blindly created.

Mitigation:
- Start with Task 0 scaffold reconciliation.
- Prefer porting tested `dev` code only if it satisfies import boundaries.
- Record any skipped scaffold files in the implementation notes.

Verification:
- `git diff --stat main...HEAD`
- `tests/test_agent_protocols.py`

### Risk: Repository protocol is too narrow

Impact: Facade may be tempted to perform raw Mongo queries or import legacy services.

Mitigation:
- Extend `AgentRepository` with domain-specific methods only.
- Keep generic query operations out of the protocol.
- Add AST import-boundary tests before implementation.

Verification:
- `uv run python -m pytest tests/test_agent_protocols.py -k import_boundary -q`

### Risk: Visibility regressions leak private agents

Impact: Discovery, matching, and public listing could expose private agent metadata or URLs.

Mitigation:
- Apply visibility before vector search and again after document hydration.
- Treat `requesting_user_id=None` as public-only.
- Keep URL masking in API/legacy adapter tests.

Verification:
- Golden discovery/list tests.
- Existing `tests/test_api_agent.py` and `tests/test_api_discovery.py`.

### Risk: Matching behavior drifts from legacy ranker

Impact: Rooms select different agents or debate mode returns too many/few agents.

Mitigation:
- Port constants and cutoff logic exactly into `agent/matching.py`.
- Test score breakdowns and cutoff behavior with deterministic vectors.
- Keep legacy `AgentSelectionService` response conversion tests.

Verification:
- `uv run python -m pytest tests/test_agent_matcher.py tests/test_agent_facade.py -k match -q`

### Risk: Vector indexing consistency changes

Impact: Registered or updated agents may disappear from matching, or deleted agents may remain matchable.

Mitigation:
- Registration/update/delete tests must assert vector upsert/delete calls.
- Hub sync must hash descriptions and reindex only when needed.
- Add rollback behavior for registration if vector upsert fails after Mongo write.

Verification:
- Golden register/delete/hub sync tests.
- Existing relay sync tests in `tests/test_api_relay.py`.

### Risk: Hub liveness circular dependency

Impact: Agent facade needs `HubLivenessReader`, while relay startup may need `AgentRegistryWriter`.

Mitigation:
- Use protocol binding, not direct imports.
- Keep the final contract simple: one async `HubLivenessReader`, one `RelayHubLivenessReader`, one bind path.
- Do not split into `HubLivenessReader` plus `HubLivenessProbe`; that was superseded by the final implementation.
- Do not use cached relay liveness for Agent hydration. `RelayHubLivenessReader.is_hub_online()` delegates to `RelayService.is_hub_alive()`.
- Allow two-phase startup binding if needed:
  - Build facade with `hub_liveness=None`.
  - Initialize relay/hub liveness.
  - Rebind or set the facade hub liveness before serving.
- Fail closed when liveness is unavailable.

Verification:
- `is_directly_callable()` tests with missing/offline/online liveness.
- Startup tests around relay initialization.

### Risk: Legacy callers expect `models.agent.Agent`

Impact: API and modules may break if they receive `AgentInfo` DTOs directly.

Mitigation:
- Keep DTO-to-legacy model conversion in `services/agent_service.py`.
- Do not put legacy model conversion in `agent/`.
- Migrate callers gradually after endpoint golden tests pass.

Verification:
- `tests/test_api_agent.py`
- `tests/test_flow_contracts.py`
- `tests/test_module_agent_dispatcher.py` if dispatcher reads agent models.

### Risk: Health job still writes through legacy Mongo singleton

Impact: Phase 3 would not fully own health status writes.

Mitigation:
- Bind health service to `AgentRepository.update_health()` or `AgentFacade.update_health()`.
- Add fail-fast behavior when the health service tries to persist before bind.

Verification:
- Health golden test.
- `tests/test_p2_modules_services.py` health sections.

### Risk: C3 fail-fast breaks unit tests that construct services directly

Impact: Existing tests may fail because they expect direct legacy service construction to work.

Mitigation:
- Update tests to bind a fake facade explicitly.
- Keep pure helpers like `normalize_agent_url()` available as wrappers.
- Use clear error text for unbound service methods.

Verification:
- `uv run python -m pytest tests/test_service_agent.py -q`

## Final Handoff Notes

Implement Phase 3 in small commits:
1. Scaffold and tests.
2. Repository.
3. Pure utilities.
4. Facade reads/lifecycle/matching/hub sync.
5. Migration adapters.
6. Container/startup wiring.
7. Golden tests and boundary gates.

Do not start by editing `api/agent.py`. The safest path is to make the new facade match legacy behavior behind `services.agent_service`, then run existing endpoint tests unchanged.
