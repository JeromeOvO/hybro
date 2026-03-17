# A2A Protocol Upgrade Roadmap

> Living document for tracking A2A protocol version upgrades.
> Last updated: 2026-03-13

## Table of Contents

- [Current State](#current-state)
- [Target: A2A v1.0](#target-a2a-v10)
- [Changes Analysis](#changes-analysis)
- [Migration Plan](#migration-plan)
- [File Impact Map](#file-impact-map)
- [Upgrade Log](#upgrade-log)

---

## Current State

### Installed SDK Versions

| Package | Pinned (pyproject.toml) | Resolved (uv.lock) | Latest on PyPI | Notes |
|---------|------------------------|---------------------|----------------|-------|
| `a2a` | `>=0.44` | `0.44` | — | Unrelated package (depends on scrapy/tabulate) — investigate and likely remove |
| `a2a-json-rpc` | `>=0.1.3` | `0.1.3` | `0.4` | JSON-RPC transport layer |
| `a2a-sdk` | `>=0.3.0` | `0.3.13` | `0.3.25` | Official A2A Python SDK (implements spec v0.3.0) |
| `a2a-server` | `>=0.1.7` | `0.5.8` | `0.6.1` | Server-side A2A framework |

### Protocol Version in Use

The codebase implements **A2A spec ~v0.2-v0.3** patterns with two parallel type systems:

**SDK client path** (`a2a_service.py` and transport layer — primary production code):
- Imports types from `a2a.types` (Message, Task, AgentCard, TextPart, FilePart, DataPart, etc.)
- JSON-RPC method names: `message/send`, `message/stream` (SDK v0.3 convention)
- Uses `context_id` on Task (current spec naming)
- Uses `kind` on SDK response objects to distinguish Task vs Message results
- Uses `TextPart`, `FilePart`, `DataPart` with `kind` discriminator (matches current spec)

**Legacy local server path** (`common/types.py`, `common/server/`, `common/client/` — used for hosting agents):
- Custom Pydantic type wrappers that duplicate and diverge from SDK types
- JSON-RPC method names: `tasks/send`, `tasks/sendSubscribe`, `tasks/get`, `tasks/cancel` (outdated, not matching current spec)
- Uses `sessionId` on Task (only in `common/types.py:66,112` — should be `context_id`)
- Custom A2A client in `common/client/client.py` (sole consumer: `common/utils/remote_agent_connection.py`)

### What Already Matches v1.0

These patterns in our codebase **already align** with the published v1.0 spec and require no migration:

- `TextPart` / `FilePart` / `DataPart` with `kind` discriminator (`common/types.py:33`, `a2a.types`)
- `TaskStatusUpdateEvent.final` field (`common/types.py:77`)
- `AgentCard.url` top-level field (`common/types.py:325`, `a2a_service.py:287`)
- JSON-RPC methods `message/send`, `message/stream` (SDK client path)
- Lowercase `TaskState` values: `submitted`, `working`, `completed`, etc. (`a2a_constants.py:42`)
- Lowercase `Role` values: `user`, `agent`
- `SendMessageConfiguration.blocking` field (`a2a_service.py:376, 1001`)
- `TaskPushNotificationConfig` as a separate type
- `AgentCapabilities.stateTransitionHistory` field
- `/.well-known/agent-card.json` as discovery path (v1.0 only supports this path; v0.3 supports both `agent.json` and `agent-card.json`)

---

## Target: A2A v1.0

**Spec release**: v1.0.0 (2026-03-12)
**Announcement**: https://a2a-protocol.org/latest/announcing-1.0/
**Changelog**: https://github.com/a2aproject/A2A/blob/main/CHANGELOG.md
**Specification**: https://a2a-protocol.org/latest/specification/
**Definitions**: https://a2a-protocol.org/latest/definitions/

> **Note**: The v1.0 spec designates a proto file as the normative source alongside JSON Schema and JSON-RPC definitions. This roadmap is written against the **published latest specification and definitions** at a2a-protocol.org, which is what the Python SDK implements and our backend uses. Always verify changes against https://a2a-protocol.org/latest/specification/ and https://a2a-protocol.org/latest/definitions/ before acting on this document.

### Backward Compatibility

The v1.0 spec is designed for progressive migration, not a hard cutover:

- **AgentCard multi-version support** — An agent can declare `supportedInterfaces` alongside the existing top-level `url`, enabling gradual interface migration
- **`A2A-Version` negotiation** — Client sends supported version; server only returns `VersionNotSupportedError` if it truly cannot serve that version
- **SDK backward compatibility** — The v1.0 changelog lists "SDK backwards compatibility mechanisms" as a feature
- **No flag day required** — Agents in the ecosystem can upgrade independently

### What's New in v1.0

| Category | Type | Change |
|----------|------|--------|
| `supportedInterfaces` | Additive | New field on `AgentCard` with per-interface `transport`, `url`, `contentTypes` (top-level `url` still exists) |
| `ListTasks` | New operation | Cursor-based pagination and filtering (`pageToken`/`nextPageToken`) |
| `SubscribeToTask` | New operation | Streaming subscription to an existing task |
| Version negotiation | New | `A2A-Version` / `A2A-Extensions` service parameters |
| Extended Agent Cards | New | Authenticated detailed cards with role-based content |
| Signed Agent Cards | New | Cryptographic verification of agent identity |
| Multi-tenancy | New | Scope/tenant field on requests for multi-tenant agent hosting |
| OAuth2 modernization | Changed | Device code + PKCE flows added; implicit/password flows **deprecated** (not removed) |

---

## Changes Analysis

Each change is categorized as Breaking, Additive, or Cleanup and mapped to its impact on our codebase.

### C-0: Discovery Path Consolidated to `agent-card.json`

**Type: BREAKING (for agents still only serving `agent.json`)**

**Spec change**: v1.0 only supports `/.well-known/agent-card.json`. The legacy `/.well-known/agent.json` path is no longer part of the spec. v0.3 agents may still serve both.

**Our current behavior**: `a2a_service.py:103-139` (`_fetch_agent_card_with_fallback`) tries `agent-card.json` first, then falls back to `agent.json`. This is already correct for talking to both v0.3 and v1.0 agents.

**Our server behavior**: `common/server/server.py:53-65` serves both paths. The `agent.json` route can be removed once all clients have upgraded, but keeping it is harmless for backward compatibility.

**Impact: LOW** — our client-side fallback already handles this correctly

### C-1: `supportedInterfaces` Added to AgentCard

**Type: ADDITIVE** — existing `url`, `protocolVersion`, `preferredTransport`, `additionalInterfaces`, and `supportsAuthenticatedExtendedCard` fields remain.

**Spec change**: AgentCard gains a `supportedInterfaces` array of `AgentInterface` objects, each with `transport`, `url`, and `contentTypes`. This enables agents to advertise multiple endpoints with different bindings.

**Impact: LOW** (additive, no breakage — but we should plan to consume this field)

| File | Reason |
|------|--------|
| `services/agent_resolver_service.py` | Could use `supportedInterfaces` for smarter endpoint selection |
| `services/a2a_service.py` | Could negotiate binding via `supportedInterfaces` |
| `common/types.py:322-333` | `AgentCard` model may need `supportedInterfaces` field when SDK exposes it |

### C-2: New `ListTasks` Operation

**Type: NEW FEATURE**

**Spec change**: New `ListTasks` method with cursor-based pagination (`pageToken`/`nextPageToken`), filtering by context, status, and timestamps.

**Impact: NEW FEATURE** — no existing code breaks

| File | Required Change |
|------|-----------------|
| `common/types.py` | Add `ListTasksRequest` / `ListTasksResponse` models (if not building on SDK types) |
| `common/server/server.py` | Add handler for `ListTasks` |
| `common/client/client.py` | Add `list_tasks()` client method (if legacy client is still in use) |

### C-3: Version Negotiation

**Type: NEW FEATURE / ADDITIVE**

**Spec change**: Clients should send `A2A-Version` service parameter. Servers may reject unsupported versions with `VersionNotSupportedError`.

**Impact: MEDIUM**

| File | Required Change |
|------|-----------------|
| `services/a2a_service.py` | Include `A2A-Version` header/parameter in outbound requests |
| `common/server/server.py` | Parse and validate `A2A-Version`; optionally return `VersionNotSupportedError` |
| `common/types.py` | Add `VersionNotSupportedError` error type |
| `api/webhooks.py` | Handle version header on inbound webhook calls |

### C-4: Extended Agent Card Support

**Type: ADDITIVE**

**Spec change**: `AgentCapabilities.extendedAgentCard` flag enables authenticated retrieval of a more detailed AgentCard. `GetExtendedAgentCard` operation added.

**Impact: LOW** (we don't currently use this, can adopt later)

### C-5: OAuth2 Flow Changes

**Type: DEPRECATION**

**Spec change**: Implicit and password flows are **deprecated** in the normative v1.0 definitions (not removed). Device code and PKCE flows added. Verify against the actual SDK surface when upgrading — the SDK may drop support before the spec formally removes them.

**Impact: LOW** (we primarily use API key / bearer token auth)

### C-6: Multi-Tenancy Support

**Type: ADDITIVE**

**Spec change**: Optional `tenant` field added to various request types for multi-tenant agent hosting.

**Impact: LOW** (additive, evaluate if/when needed)

### C-7: Signed Agent Cards

**Type: ADDITIVE**

**Spec change**: `AgentCard.signatures` field for cryptographic verification of agent identity.

**Impact: LOW** (additive, can adopt for trusted agent discovery)

---

## Legacy Cleanup (Not v1.0-Specific, Can Start Now)

These are pre-existing issues in our codebase that should be addressed regardless of the v1.0 upgrade. Fixing them reduces the migration surface for any future spec changes.

### L-1: Duplicate Type System in `common/types.py`

The custom types in `common/types.py` duplicate types already provided by `a2a.types` (from `a2a-sdk`), with divergences that create confusion.

**Key divergences from current SDK types:**

| Custom type (`common/types.py`) | SDK type (`a2a.types`) | Divergence |
|--------------------------------|------------------------|------------|
| `Task.sessionId` (line 66) | `Task.context_id` | Wrong field name — SDK already uses `context_id` |
| `Task.kind = "task"` (line 65) | `Task.kind` | Same, but custom type hardcodes default |
| `TaskSendParams.sessionId` (line 112) | `MessageSendParams.message` | Entirely different structure |
| `SendTaskRequest.method = "tasks/send"` (line 150) | `SendMessageRequest.method = "message/send"` | Different method names |
| `SendTaskStreamingRequest.method = "tasks/sendSubscribe"` (line 163) | `SendStreamingMessageRequest.method = "message/stream"` | Different method names |
| `TaskResubscriptionRequest.method = "tasks/resubscribe"` (line 216) | — | Not in SDK |

**Consumers of `common/types.py`** (only the legacy server path):
- `common/client/client.py:16`
- `common/client/card_resolver.py:6`
- `common/server/server.py:23`
- `common/server/task_manager.py:29`
- `common/utils/remote_agent_connection.py:14`

The production client path (`a2a_service.py`, transports, etc.) already imports from `a2a.types`.

### L-2: Legacy Custom A2A Client

`common/client/client.py` is a hand-rolled A2A client that duplicates the official SDK's `a2a.client.A2AClient`. It has only one consumer: `common/utils/remote_agent_connection.py:13`.

### L-3: `a2a>=0.44` Package

The `a2a` package (v0.44) depends on `scrapy` and `tabulate` — it appears to be an unrelated package, not the A2A protocol SDK. Investigate whether it's actually used and remove if not.

---

## Migration Plan

### Phase 0: Preparation (Can Start Now)

**Goal**: Clean up technical debt and reduce migration surface.

- [ ] **0.1**: Investigate and likely remove the `a2a>=0.44` package (L-3)
- [ ] **0.2**: Audit `common/types.py` — identify which custom types are still needed vs. which can be replaced by `a2a.types` imports (L-1)
- [ ] **0.3**: Remove or deprecate `common/client/client.py` — migrate its sole consumer (`common/utils/remote_agent_connection.py`) to the official `a2a.client.A2AClient` from `a2a-sdk` (L-2)
- [ ] **0.4**: Fix `sessionId` -> `context_id` in `common/types.py` to match the SDK and spec (L-1)
- [ ] **0.5**: Align legacy method names (`tasks/send` etc.) to match the SDK's `message/send` convention, or plan to retire the legacy server path entirely (L-1)
- [ ] **0.6**: Add integration tests that validate A2A request/response round-trips against a mock agent
- [ ] **0.7**: Update locked SDK versions to latest: `a2a-sdk>=0.3.25`, `a2a-json-rpc>=0.4`, `a2a-server>=0.6.1`

### Phase 1: v1.0 SDK Upgrade

**Goal**: Upgrade to the v1.0-compatible SDK release when available.

- [ ] **1.1**: Monitor `a2a-sdk` PyPI releases for v1.0 compatibility (currently 0.3.25)
- [ ] **1.2**: When available, upgrade `a2a-sdk`, `a2a-json-rpc`, `a2a-server` in `pyproject.toml`
- [ ] **1.3**: Run `uv sync` and fix any import errors or type mismatches
- [ ] **1.4**: Update `common/types.py` if the SDK's Pydantic models surface new required fields
- [ ] **1.5**: Run existing tests and fix failures
- [ ] **1.6**: Add `VersionNotSupportedError` to error types (C-3)

### Phase 2: Adopt New v1.0 Features

**Goal**: Leverage new capabilities enabled by v1.0.

- [ ] **2.1**: Add `A2A-Version` service parameter to outbound requests (C-3)
- [ ] **2.2**: Handle `A2A-Version` on inbound requests in `common/server/server.py` (C-3)
- [ ] **2.3**: Implement `ListTasks` operation (C-2) — client and server
- [ ] **2.4**: Implement `SubscribeToTask` for re-subscribing to existing tasks
- [ ] **2.5**: Consume `supportedInterfaces` from AgentCard for smarter endpoint selection (C-1)
- [ ] **2.6**: Add Extended Agent Card support (C-4)
- [ ] **2.7**: Add Signed Agent Card verification for trusted agent discovery (C-7)
- [ ] **2.8**: Evaluate multi-tenancy support needs (C-6)

### Phase 3: Legacy Cleanup

**Goal**: Remove deprecated patterns once all connected agents support v1.0.

- [ ] **3.1**: Remove `common/client/client.py` if not done in Phase 0
- [ ] **3.2**: Clean up `common/types.py` — remove types fully provided by `a2a-sdk`
- [ ] **3.3**: Retire legacy method names if local server has been updated

---

## File Impact Map

Quick reference of all files affected by the v1.0 migration and legacy cleanup, sorted by priority.

### High Priority (Legacy Cleanup — Can Start Now)

| File | Reason |
|------|--------|
| `common/types.py` | Duplicate type wrappers with wrong field names (`sessionId`), outdated method names (`tasks/send`), divergent from SDK |
| `common/client/client.py` | Entire legacy client should be replaced by SDK client |
| `common/server/server.py` | Uses custom types from `common/types.py` with outdated method routing |
| `common/server/task_manager.py` | Uses custom types, references `sessionId` |
| `common/utils/remote_agent_connection.py` | Sole consumer of legacy client |

### Medium Priority (v1.0 Feature Adoption)

| File | Reason |
|------|--------|
| `services/a2a_service.py` | Add `A2A-Version` parameter, consume `supportedInterfaces` |
| `services/agent_resolver_service.py` | Could use `supportedInterfaces` for endpoint resolution |
| `api/webhooks.py` | Handle `A2A-Version` header on inbound webhooks |

### Low Priority (Additive / Future)

| File | Reason |
|------|--------|
| `modules/RoomMessageCenter.py` | May need updates for `ListTasks` / `SubscribeToTask` |
| `modules/TaskStateManager.py` | May expose new task listing features |
| `services/agent_service.py` | Could expose `supportedInterfaces` in agent metadata |

### Unaffected by v1.0 (Confirmed Aligned)

These files use patterns that match the current v1.0 spec and need no changes:

| File | Why It's Fine |
|------|---------------|
| `services/a2a_constants.py` | `TaskState` enum values are lowercase — matches spec |
| `api/a2a_tasks.py` | Serializes `state.value` — lowercase values match spec |
| `services/sse_services.py` | Emits lowercase task states via SSE — matches spec |
| `modules/transports/webhook.py` | Uses structural parsing (`"task" in payload`) — already correct |
| `modules/agent_response_handler.py` | Internal `AgentEvent.kind` routing — unrelated to A2A spec `kind` |
| `modules/transports/direct.py` | `result.kind` checks on SDK types — `kind` still exists in spec |
| `modules/WorkflowCenter.py` | `process_response.kind` checks — `kind` still exists in spec |
| `common/utils/a2a_helpers.py` | Part extraction using `TextPart`/`FilePart`/`DataPart` — matches spec |

---

## Dual-Version Strategy

The v1.0 spec has first-class support for progressive migration:

1. **AgentCard**: v1.0 agents may declare `supportedInterfaces` alongside the existing top-level `url`. Our code can continue to use `agent_card.url` and optionally consume `supportedInterfaces` for enhanced routing.
2. **Client-side**: The SDK handles version negotiation via `A2A-Version` header.
3. **Server-side**: Our AgentCard continues to work as-is. Add `supportedInterfaces` when ready.
4. **No enum/method/type migration needed**: The JSON-RPC binding retains the same wire format (lowercase enums, `kind` discriminator, `message/send` methods).

```
                    +----------------+
                    | Our Backend    |
                    | (client role)  |
                    +-------+--------+
                            |
              +-------------+-------------+
              |                           |
        v0.3 agents                 v1.0 agents
        agent_card.url              agent_card.url + supportedInterfaces
        message/send                message/send
        kind-based responses        kind-based responses
        /.well-known/agent.json     /.well-known/agent-card.json
        OR agent-card.json          (agent.json no longer supported)
```

---

## Upgrade Log

> Record completed upgrades here for historical reference.

### [Template]

```
#### vX.Y -> vX.Z -- YYYY-MM-DD

**SDK versions**: a2a-sdk X.Y.Z, a2a-json-rpc X.Y.Z, a2a-server X.Y.Z
**Changes addressed**: C-1, C-3, ...
**Files modified**: list of files
**Tests**: passing / failing (details)
**Notes**: any issues encountered
```

---

*This document should be updated whenever A2A protocol upgrades are planned or executed.*
