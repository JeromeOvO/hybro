# A2A Protocol Upgrade Roadmap (Frontend)

> Living document tracking migration of `@a2a-js/sdk` across A2A protocol versions.
> Scope: `hybro-frontend/` only. For backend migration, see the backend repo's equivalent doc.

---

## Current State

| Item | Value |
|------|-------|
| SDK package | `@a2a-js/sdk` |
| Pinned version | `^0.3.10` |
| Protocol version implemented | v0.3 |
| Target protocol version | **v1.0** |
| SDK v1.0 availability | Not yet released (latest npm dist-tag: 0.3.12; repo pinned to `^0.3.10`) |

## How to Use This Document

1. When a new A2A spec version is announced, add a new `## Migration: vX.Y -> vZ.W` section.
2. Fill in the impact analysis using the **Affected Surface** inventory below.
3. Work through phases in order. Check off items as completed.
4. After migration is complete, update **Current State** and archive the migration section.

---

## Frontend A2A Surface Inventory

All frontend files that depend on `@a2a-js/sdk` types. Use this as a checklist when assessing any A2A upgrade.

### Type Hub Files (re-export SDK types to the rest of the app)

| File | SDK Types Used | Role |
|------|---------------|------|
| `src/lib/types/request.ts` | `TaskState`, `Part`, `TextPart`, `FilePart`, `DataPart`, `FileWithBytes`, `FileWithUri`, `Message`, `Task`, `TaskStatus`, `Artifact`, `AgentCard`, `AgentCapabilities`, `AgentExtension`, `AgentInterface`, `AgentProvider`, `AgentSkill`, `AgentCardSignature`, `SecurityScheme`, `APIKeySecurityScheme`, `HTTPAuthSecurityScheme`, `OAuth2SecurityScheme`, `OpenIdConnectSecurityScheme`, `MutualTLSSecurityScheme`, `OAuthFlows`, `AuthorizationCodeOAuthFlow`, `ClientCredentialsOAuthFlow`, `ImplicitOAuthFlow`, `PasswordOAuthFlow` | Primary type re-export hub |
| `src/lib/types/response.ts` | Same core types + `JSONRPCErrorResponse`, `JSONRPCError`, `JSONParseError`, `InvalidRequestError`, `MethodNotFoundError`, `InvalidParamsError`, `InternalError`, `TaskNotFoundError`, `TaskNotCancelableError`, `PushNotificationNotSupportedError`, `UnsupportedOperationError`, `ContentTypeNotSupportedError`, `InvalidAgentResponseError`, `TaskStatusUpdateEvent`, `TaskArtifactUpdateEvent`, `SendMessageResponse`, `SendStreamingMessageResponse`, `SendMessageSuccessResponse`, `SendStreamingMessageSuccessResponse` | Response type re-export hub |
| `src/lib/types/sse.ts` | `TaskState` | Task state constants, helpers |
| `src/lib/types/agent.ts` | `AgentCard`, `AgentCapabilities`, `AgentExtension`, `AgentProvider`, `AgentSkill`, `SecurityScheme`, OAuth types (note: does NOT re-export `AgentInterface`, `AgentCardSignature`, or `MutualTLSSecurityScheme` — those are only in `request.ts`/`response.ts`) | Agent type re-export hub |
| `src/lib/types/room.ts` | `Message`, `Task`, `TaskStatus`, `Part`, `TextPart`, `FilePart`, `DataPart`, `FileWithBytes`, `FileWithUri`, `TaskState` | Room type re-export hub |

### Consumer Files (use SDK types indirectly via hub files)

| Category | Files | A2A Concepts Used |
|----------|-------|-------------------|
| **AgentCard rendering** | `agent-card.tsx`, `consumer-agent-card.tsx`, `agent-selector.tsx`, `room-default-agents-editor.tsx`, `group-management-modal.tsx`, `workflow-message.tsx`, `developer-sidebar.tsx`, `d/page.tsx`, `d/agents/page.tsx`, `d/agents/[id]/page.tsx`, `c/agents/[id]/page.tsx`, `c/room/[id]/page.tsx`, `d/register/page.tsx`, `c/chat/page.tsx`, `c/agents/page.tsx`, `settings/hub-section.tsx`, `useChatRoomCreation.ts` | `agent_card.name`, `.description`, `.iconUrl`, `.version`, `.provider.organization`, `.capabilities.*` (all sub-fields), `.skills`, `.defaultInputModes`, `.defaultOutputModes`, `.url`, `.documentationUrl` |
| **Task state** | `task-status-message.tsx`, `room-messages.tsx`, `useRoomWebhook.ts`, `stores/message-store/stale-detection.ts` | `TaskState`, `TASK_STATE.*`, `isTerminalState()`, `isFailureState()`, `isInteractiveState()` |
| **Artifact / Part rendering** | `artifact-renderer.tsx`, `artifact-list.tsx`, `part-renderer.tsx`, `message-bubble.tsx` | `ArtifactPart.kind` (`text`/`file`/`data`), `.text`, `.file`, `.data` |
| **A2A task queries** | `src/lib/api/a2a-tasks.ts` | `Task.id`, `Task.contextId`, `Task.status.state`, `Task.status.message`, `Task.artifacts`, `Artifact.artifactId`, `Artifact.parts` |
| **Message store** | `stores/message-store/types.ts`, `upsert.ts`, `convert-api-message.ts`, `resolve-display-type.ts`, `stale-detection.ts`, `index.ts` | `TaskState`, artifact structure |
| **Developer docs** | `developer-docs-content.tsx` | Code samples showing `AgentCard` construction (Python examples) |

---

## Migration: v0.3 -> v1.0

### Important: Published Spec vs Announcement

The v1.0 announcement describes high-level goals (multiple protocol bindings, signed agent
cards, enterprise features). However, the **current published latest spec** has not yet
changed the Part model, AgentCard shape, enum wire format, or JSON-RPC method names from
their v0.3 definitions. The tables below reflect what the current published spec actually
defines, with unconfirmed announcement-level changes tracked separately in the Watch List.

**Always verify changes against the published spec and SDK changelog before starting
migration work.** Do not rewrite code based on announcement language alone.

### Confirmed in Current Published Spec

These are fields/features that the current published spec defines and the frontend already
uses or will need to support.

| Area | Current Published Shape | Frontend Status |
|------|------------------------|-----------------|
| **Part types** | `TextPart \| FilePart \| DataPart` with `kind` discriminator | Matches. `types.ts:7` uses `kind`, `part-renderer.tsx:142` switches on it. No migration needed yet. |
| **AgentCard.url** | Top-level `url` field still present, alongside `protocolVersion`, `preferredTransport` | Matches. `d/register/page.tsx:544` renders it directly. Stable. |
| **AgentCard.additionalInterfaces** | `additionalInterfaces?: AgentInterface[]` where `AgentInterface` has `{ transport, url, contentTypes }` | Not yet consumed by frontend. Opportunity to display in agent detail views. |
| **AgentCard.supportsAuthenticatedExtendedCard** | Boolean flag for extended card support | Not yet consumed. Opportunity for authenticated agent profiles. |
| **defaultInputModes / defaultOutputModes** | Unchanged, same field names | Matches. 6 components render them (see Appendix). Stable. |
| **capabilities.stateTransitionHistory** | Still present in the published spec | Matches. Read by `agent-selector.tsx:114` and `d/register/page.tsx:527`. Stable. |
| **TaskState values** | Lowercase strings: `"submitted"`, `"working"`, `"input-required"`, `"auth-required"`, `"completed"`, `"failed"`, `"canceled"`, `"rejected"` | Matches. `sse.ts:83` hardcodes these. Stable. |
| **Role values** | `"user"`, `"agent"` | Matches. Used in SSE and type definitions. Stable. |
| **JSON-RPC methods** | `message/send`, `tasks/get`, `tasks/cancel`, etc. | Frontend doesn't call directly (backend handles). Stable. |
| **Discovery** | Primary: `/.well-known/agent-card.json`; fallback: `/.well-known/agent.json` | Frontend doesn't fetch directly (backend handles). Stable. |

### Watch List: Announced but Not Yet in Published Spec

The v1.0 announcement describes these changes at a high level. They are **not yet reflected
in the published spec** and must not drive code rewrites until confirmed in the SDK.

| Announced Change | Potential Frontend Impact | Action |
|-----------------|--------------------------|--------|
| **Multiple protocol bindings** (JSON-RPC, gRPC, HTTP+REST) | May change how `AgentCard` exposes endpoints. Currently `additionalInterfaces[]` already exists for this. Watch for `url` removal or restructuring. | Monitor SDK releases. Do NOT remove `agent_card.url` preemptively. |
| **Signed Agent Cards** (cryptographic verification) | Could enable "verified agent" badges in UI | Wait for `AgentCardSignature` to appear in SDK types. |
| **Part model changes** (announcement mentions "breaking changes in interaction protocol") | If Part shape changes, `types.ts:7`, `part-renderer.tsx:142`, `convert-api-message.ts:147`, `message-bubble.tsx:645` would need rewriting. | Monitor. Current published spec still uses `TextPart \| FilePart \| DataPart` with `kind`. Do not preemptively rewrite. |
| **Enum wire format changes** (announcement mentions "modernized patterns") | If `TaskState` string values change, `sse.ts:83` constants and bare `as TaskState` casts in `useRoomWebhook.ts:753` would break silently. | Monitor. Current published values are still lowercase/kebab-case (`"input-required"`, `"user"`, etc.). Do not preemptively add normalization. |
| **Extended Agent Card** (authenticated detailed metadata) | Could expose richer agent profiles | Wait for `getExtendedAgentCard` to appear in SDK. |

### Confirmed New Features (Frontend-Relevant)

| Feature | Frontend Opportunity | Priority |
|---------|---------------------|----------|
| **`contextId`** on Message/Task | Group multi-turn conversations; could enhance room UX | MEDIUM |
| **`referenceTaskIds`** on Message | Cross-task linking in UI | LOW |
| **`additionalInterfaces`** on AgentCard | Display multiple protocol endpoints in agent detail views | LOW |
| **`supportsAuthenticatedExtendedCard`** on AgentCard | Gate "view full profile" button | LOW |
| **`ListTasks` with cursor pagination** | Could improve `a2a-tasks.ts` pagination | LOW |
| **`SubscribeToTask`** persistent streaming | Could replace polling in task status UI | MEDIUM |

### Migration Phases

#### Phase 0: Pre-Migration (Do Now)
> Can be done before the v1.0 SDK is released.
> Items 1 and 2 below are independent and can be done in parallel on separate branches.

- [ ] **Pin `@a2a-js/sdk` version**: Change `^0.3.10` to `~0.3.10` in `package.json` to prevent accidental minor-version bumps that could introduce breaking changes before we're ready.
- [ ] **Centralize AgentCard field access**: Many components (17+) directly access `agent.agent_card.name`, `.description`, etc. Create a thin accessor layer so that if AgentCard fields are renamed or restructured in a future SDK version, changes are isolated to one file.
  - Files: All components listed in "AgentCard rendering" above
- [ ] **Audit `a2a-tasks.ts` inline types**: This file defines its own inline A2A types (e.g., `A2ATaskStatus.task.artifacts`) rather than importing from the SDK. SDK type changes will NOT cause compilation errors here — the types will silently drift. Consider importing SDK types directly or adding a comment flagging them for manual review on each SDK bump.

> **Note on Part types**: The current published spec still defines Part as `TextPart | FilePart | DataPart` with `kind` discriminator. The frontend's `ArtifactPart` in `types.ts:7`, `switch (part.kind)` in `part-renderer.tsx:142`, and `root.kind` read in `convert-api-message.ts:147` all match this shape. **Do not preemptively rewrite these** — wait for the SDK to confirm any shape change.

#### Phase 1: SDK Upgrade
> When `@a2a-js/sdk` v1.0 is released. **Read the SDK changelog first** to identify
> actual breaking changes before touching any code.

- [ ] **Read SDK changelog**: Check the `@a2a-js/sdk` v1.0 release notes for TypeScript-specific breaking changes, renamed exports, removed types, and new types. Cross-reference against the Watch List above.
- [ ] **Update `package.json`**: Bump `@a2a-js/sdk` to v1.0.
- [ ] **Fix type hub compilation errors**: Update all 5 type hub files to match new SDK exports:
  - `src/lib/types/request.ts`
  - `src/lib/types/response.ts`
  - `src/lib/types/sse.ts`
  - `src/lib/types/agent.ts`
  - `src/lib/types/room.ts`
- [ ] **Check `TaskState` wire values**: The frontend hardcodes lowercase/kebab-case values in `sse.ts:83` (`"submitted"`, `"input-required"`, etc.) and casts raw SSE strings directly to `TaskState` in `useRoomWebhook.ts:753` (`sseMessage.data.status as TaskState`). If the SDK changes enum string values, these casts will silently produce invalid values. **If values changed**: (1) Update `TASK_STATE` constants in `sse.ts`; (2) Add a `normalizeTaskState(raw: string): TaskState` function that accepts both old and new wire values for rollout safety; (3) Replace bare `as TaskState` casts in `useRoomWebhook.ts` with calls to `normalizeTaskState()`. **If values unchanged**: no action needed — current code is correct.
- [ ] **Check `AgentCard` shape**: Use the SDK types to identify any field changes. Key fields to verify:
  - `url` — still present in current published spec. If removed, create a `getPrimaryEndpoint(card)` helper and update `d/register/page.tsx:544`.
  - `additionalInterfaces` — uses `AgentInterface { transport, url, contentTypes }`. If promoted to required or if `url` is removed, frontend needs to resolve endpoints from this array.
  - `capabilities.stateTransitionHistory` — stable in current published spec. Read by `agent-selector.tsx:114` and `d/register/page.tsx:527`.
  - `capabilities.pushNotifications` — check camelCase vs snake_case consistency.
  - `documentationUrl` — only used in `c/agents/[id]/page.tsx`.
- [ ] **Check Part/Artifact types**: If the SDK changes Part from `TextPart | FilePart | DataPart` with `kind`:
  - Update `ArtifactPart` interface in `stores/message-store/types.ts:7`
  - Update `stores/message-store/convert-api-message.ts:147` (reads `root.kind`, constructs parts)
  - Update `part-renderer.tsx:142` (`switch (part.kind)` dispatch)
  - Update `message-bubble.tsx:645` (`p.kind === 'text'` deduplication)
  - **If Part shape is unchanged**: no action needed — current code matches.
- [ ] **Check JSON-RPC error types**: Look for new error types (e.g., `VersionNotSupportedError`, `ExtensionSupportRequiredError`) and update re-exports in `response.ts`.
- [ ] **Run `npm run build`**: Fix all remaining TypeScript errors.
- [ ] **Run `npm run test`**: Fix all failing unit tests.
- [ ] **Run `npm run test:e2e`**: Verify end-to-end flows still work.

#### Phase 2: Adopt New Features (Optional)
> After Phase 1 is stable. Each item is independent.

- [ ] **Display `additionalInterfaces`**: Show multiple protocol endpoints in agent detail views (`c/agents/[id]/page.tsx`, `d/register/page.tsx`).
- [ ] **Display `contextId`**: Show conversation context grouping in room UI if backend starts returning it.
- [ ] **Extended agent cards**: If `supportsAuthenticatedExtendedCard` is true, gate a "view full profile" action.
- [ ] **Verification badges**: Use `AgentCardSignature` (when available in SDK) to show a "verified" badge.
- [ ] **Cursor-based task pagination**: Update `a2a-tasks.ts` to use `pageToken`/`nextPageToken` if backend adopts v1.0 ListTasks.
- [ ] **Update developer docs**: Rewrite code samples in `developer-docs-content.tsx` to reflect any v1.0 AgentCard changes.

#### Phase 3: Cleanup
> After v1.0 adoption is confirmed stable in production.

- [ ] **Remove compatibility shims**: Delete any normalization layers or field aliases added during migration.
- [ ] **Update this document**: Move Current State to v1.0. Archive this migration section.
- [ ] **Update `CLAUDE.md`**: Reflect any new patterns or conventions from the migration.

### Testing Strategy

| Test Type | What to Verify |
|-----------|---------------|
| **TypeScript compilation** (`npm run build`) | All SDK type imports resolve; no type errors |
| **Unit tests** (`npm run test`) | Task state helpers, message store upsert/hydration, display type resolution, stale detection |
| **Component rendering** | Agent cards render correctly; part-renderer handles all Part types; artifact list displays properly |
| **E2E** (`npm run test:e2e`) | Room creation, message send/receive, task status updates via SSE, agent browsing |

### Rollback Plan

If critical issues are found after SDK upgrade:
1. Revert `package.json` to previous `@a2a-js/sdk` version.
2. Revert type hub file changes.
3. Run `npm install && npm run build && npm run test` to confirm rollback.
4. Document the issue in this file under a "Known Issues" section.

---

## Appendix: AgentCard Field Usage Map

Detailed map of which `AgentCard` fields are accessed in each component, for quick impact assessment on any AgentCard schema change.

To regenerate this inventory:
```bash
grep -rn "agent_card\." src/ --include="*.tsx" --include="*.ts" | grep -v node_modules | grep -v "\.test\."
grep -rn "@a2a-js/sdk" src/ --include="*.tsx" --include="*.ts" -l
```

```
agent_card.name
  -> agent-card.tsx, consumer-agent-card.tsx, agent-selector.tsx,
     room-default-agents-editor.tsx, group-management-modal.tsx,
     workflow-message.tsx, d/page.tsx, d/agents/page.tsx,
     d/agents/[id]/page.tsx, c/agents/[id]/page.tsx, d/register/page.tsx,
     c/chat/page.tsx, c/room/[id]/page.tsx, c/agents/page.tsx,
     developer-sidebar.tsx, settings/hub-section.tsx, useChatRoomCreation.ts

agent_card.description
  -> agent-card.tsx, consumer-agent-card.tsx, agent-selector.tsx,
     room-default-agents-editor.tsx, group-management-modal.tsx,
     workflow-message.tsx, d/page.tsx, d/agents/page.tsx,
     d/register/page.tsx, c/agents/page.tsx, c/agents/[id]/page.tsx,
     settings/hub-section.tsx

agent_card.iconUrl
  -> agent-card.tsx, consumer-agent-card.tsx, agent-selector.tsx,
     room-default-agents-editor.tsx, group-management-modal.tsx,
     d/agents/[id]/page.tsx, c/agents/[id]/page.tsx,
     c/room/[id]/page.tsx, c/chat/page.tsx

agent_card.version
  -> agent-selector.tsx, d/agents/[id]/page.tsx, c/agents/[id]/page.tsx,
     d/register/page.tsx

agent_card.provider.organization
  -> consumer-agent-card.tsx, agent-selector.tsx, workflow-message.tsx,
     d/page.tsx, d/agents/page.tsx, d/agents/[id]/page.tsx,
     c/agents/[id]/page.tsx, d/register/page.tsx

agent_card.capabilities.streaming
  -> agent-selector.tsx, d/register/page.tsx, c/agents/[id]/page.tsx

agent_card.capabilities.pushNotifications
  -> agent-selector.tsx, d/register/page.tsx, c/agents/[id]/page.tsx

agent_card.capabilities.stateTransitionHistory
  -> agent-selector.tsx, d/register/page.tsx, c/agents/[id]/page.tsx

agent_card.capabilities.extensions
  -> d/register/page.tsx, c/agents/[id]/page.tsx

agent_card.skills
  -> agent-selector.tsx, d/register/page.tsx, c/agents/page.tsx,
     c/agents/[id]/page.tsx

agent_card.defaultInputModes
  -> consumer-agent-card.tsx, agent-selector.tsx,
     room-default-agents-editor.tsx, group-management-modal.tsx,
     d/register/page.tsx, c/agents/[id]/page.tsx

agent_card.defaultOutputModes
  -> consumer-agent-card.tsx, agent-selector.tsx,
     room-default-agents-editor.tsx, group-management-modal.tsx,
     d/register/page.tsx, c/agents/[id]/page.tsx

agent_card.url
  -> d/register/page.tsx

agent_card.documentationUrl
  -> c/agents/[id]/page.tsx
```

---

## Version History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-12 | — | Initial document: v0.3 -> v1.0 roadmap |
| 2026-03-12 | — | Review round 1: added missing files, `documentationUrl`, `convert-api-message.ts`, `a2a-tasks.ts` drift warning, inventory regen commands |
| 2026-03-12 | — | Review round 2: precise Part migration description; TaskState normalization layer; concrete `interfaces[]` plan; added `c/room/[id]/page.tsx` to main table |
| 2026-03-12 | — | Review round 3: corrected Part shape, TaskState enums, AgentCard url, JSON-RPC methods (subsequently reverted in round 4) |
| 2026-03-12 | — | Review round 4: grounded migration section against published spec; separated confirmed from Watch List; fixed SDK version to 0.3.12 |
| 2026-03-12 | — | Review round 5: removed orphaned duplicate `#### New Features` and `#### Deprecated / Removed` sections (leftover from round 4 partial replacement); removed speculative wire format details from Watch List; fixed `stateTransitionHistory` to stable (not conditional) in Phase 1 |
