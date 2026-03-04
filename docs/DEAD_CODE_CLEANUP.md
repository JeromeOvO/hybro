# Dead Code Cleanup — Removal Plan

> **Status: Not Started** — Design approved, pending execution. ~1,675 lines across 13+ files.

**Depends on**: None
**Decoupled from**: All other frontend design docs

---

## 1. Problem Statement

The frontend codebase contains approximately 1,675 lines of dead code across 13+ files.
This code is unreachable — no live imports reference it. It consists of:

- A legacy workflow subsystem (hook, components, API functions) superseded by the
  SSE-based room message flow.
- Legacy API route proxies (Next.js API routes) that are no longer used since the
  frontend calls the backend directly.
- Orphaned API functions (`createAndParseUserMessage`, memory API, orchestration API)
  that are defined but never imported.
- Deprecated auth stubs.

Dead code creates confusion for developers navigating the codebase, increases bundle
size, and makes it harder to identify which code paths are actually in use.

---

## 2. Dead Code Inventory

### 2.1 Workflow Subsystem (entirely dead)

| # | File | Lines | Status | Why Dead |
|---|---|---|---|---|
| 1 | `src/hooks/useWorkflow.ts` | 324 | Delete file | Only imported by `workflow-container.tsx` (dead). Imports orchestration API functions (dead). |
| 2 | `src/components/workflow-container.tsx` | 53 | Delete file | Only consumer of `useWorkflow`. Zero imports found in `src/`. |
| 3 | `src/components/workflow-message.tsx` | 254 | Delete file | Exports `WorkflowStage` type + `WORKFLOW_STAGE` constant. Only imported by `useWorkflow.ts` and `workflow-container.tsx` (both dead). |

**Total**: 631 lines.

**Kill chain**: `workflow-message.tsx` -> `useWorkflow.ts` -> `workflow-container.tsx`.
None of these files are imported by any page, layout, or live component.

### 2.2 Legacy API Route Proxies (entirely dead)

These are Next.js API route handlers under `src/app/api/` that proxy requests to the
backend. The frontend now calls the backend directly via `src/lib/api/` functions using
`getApiUrl()`. No client code references these `/api/` routes.

| # | File | Lines | Status |
|---|---|---|---|
| 4 | `src/app/api/orchestrationCenter/[...endpoint]/route.ts` | 93 | Delete file + dir |
| 5 | `src/app/api/memory/[...endpoint]/route.ts` | 78 | Delete file + dir |
| 6 | `src/app/api/agent/[...endpoint]/route.ts` | 76 | Delete file + dir |
| 7 | `src/app/api/roomCenter/[...endpoint]/route.ts` | 105 | Delete file + dir |
| 8 | `src/app/api/sse/[...endpoint]/route.ts` | 252 | Delete file + dir |
| 9 | `src/app/api/inspectionCenter/[...endpoint]/route.ts` | 76 | Delete file + dir |
| 10 | `src/app/api/task/[...endpoint]/route.ts` | 76 | Delete file + dir |
| 11 | `src/app/api/health/route.ts` | 34 | Delete file + dir |

**Total**: 790 lines across 8 files.

**Verification**: Run `rg '/api/orchestrationCenter\|/api/memory\|/api/agent\|/api/roomCenter\|/api/sse\|/api/inspectionCenter\|/api/task\|/api/health' src/ --type ts --type tsx`
to confirm zero client-side references to these routes. Note: the backend URL pattern
(`/api/v1/...`) is different from the Next.js proxy pattern (`/api/...`), so there is no
ambiguity.

### 2.3 Dead API Functions (in live files)

| # | File | Function(s) | Lines | Status |
|---|---|---|---|---|
| 12 | `src/lib/api/orchestration.ts` | All 7 functions: `decomposeTask`, `assignAgentsToMetaTasks`, `assignAgentToMetaTask`, `runWorkflow`, `retryMetaTask`, `summarizeMetaTaskForBaseTask`, `processRoomUserMessage` | 147 | Delete entire file |
| 13 | `src/lib/api/memory.ts` | All 4 functions: `addChatContext`, `getChatContextBySessionId`, `updateChatContextBySessionId`, `deleteChatContextBySessionId` | 57 | Delete entire file |
| 14 | `src/lib/api/room.ts` | `createAndParseUserMessage` | ~40 | Remove function only |

**Total**: ~244 lines.

`orchestration.ts` and `memory.ts` are entirely dead (no live consumers). They are
re-exported via `src/lib/api/index.ts` barrel, but no live code imports those exports.

`room.ts` is live — other functions like `SendMessage`, `createNewRoom`,
`inquiryRoomSetting` are actively used. Only `createAndParseUserMessage` is dead
(superseded by the unified `SendMessage`).

### 2.4 Deprecated Auth Stubs (in live file)

| # | File | Function(s) | Lines | Status |
|---|---|---|---|---|
| 15 | `src/lib/auth.ts` | `getAuthToken`, `getAuthHeaders` | ~10 | Remove functions only |

These are explicitly marked as stubs (`@deprecated`) returning `null` / `{}`. They are
not imported anywhere. The live functions in the same file (`setDefaultGetToken`,
`getClientAuthHeaders`) must be preserved.

### 2.5 Barrel Re-exports

| # | File | Change | Lines |
|---|---|---|---|
| 16 | `src/lib/api/index.ts` | Remove `export * from './orchestration'` and `export * from './memory'` lines | ~2 |

---

## 3. Summary

| Category | Files | Lines |
|---|---|---|
| Workflow subsystem | 3 files | 631 |
| Legacy API routes | 8 files | 790 |
| Dead API modules | 2 files | 204 |
| Dead functions in live files | 2 files | ~50 |
| Barrel cleanup | 1 file | ~2 |
| **Total** | **13 files deleted + 3 files edited** | **~1,677** |

---

## 4. Execution Plan

### Phase 1: Verify (read-only)

Before any deletion, run verification commands to confirm each item is truly dead:

```bash
# 1. Workflow subsystem
rg "useWorkflow" src/ --type-add 'web:*.{ts,tsx}' --type web
rg "workflow-container" src/ --type-add 'web:*.{ts,tsx}' --type web
rg "workflow-message\|WorkflowStage\|WORKFLOW_STAGE" src/ --type-add 'web:*.{ts,tsx}' --type web

# 2. Orchestration API
rg "decomposeTask\|assignAgentsToMetaTasks\|assignAgentToMetaTask\|runWorkflow\|retryMetaTask\|summarizeMetaTaskForBaseTask\|processRoomUserMessage" src/ --type-add 'web:*.{ts,tsx}' --type web

# 3. Memory API
rg "addChatContext\|getChatContextBySessionId\|updateChatContextBySessionId\|deleteChatContextBySessionId" src/ --type-add 'web:*.{ts,tsx}' --type web

# 4. Legacy API routes (check for client-side references)
rg "fetch.*['\"]\/api\/(orchestrationCenter|memory|agent|roomCenter|sse|inspectionCenter|task|health)" src/ --type-add 'web:*.{ts,tsx}' --type web

# 5. Dead function in room.ts
rg "createAndParseUserMessage" src/ --type-add 'web:*.{ts,tsx}' --type web

# 6. Auth stubs
rg "getAuthToken\|getAuthHeaders" src/ --type-add 'web:*.{ts,tsx}' --type web
```

Expected: each command returns only hits in the files being deleted (self-references),
or zero hits.

### Phase 2: Delete entire files

Delete the following files and their parent directories (if empty after deletion):

```
src/hooks/useWorkflow.ts
src/components/workflow-container.tsx
src/components/workflow-message.tsx
src/lib/api/orchestration.ts
src/lib/api/memory.ts
src/app/api/orchestrationCenter/[...endpoint]/route.ts
src/app/api/memory/[...endpoint]/route.ts
src/app/api/agent/[...endpoint]/route.ts
src/app/api/roomCenter/[...endpoint]/route.ts
src/app/api/sse/[...endpoint]/route.ts
src/app/api/inspectionCenter/[...endpoint]/route.ts
src/app/api/task/[...endpoint]/route.ts
src/app/api/health/route.ts
```

After deleting the route files, remove the now-empty directories:

```
src/app/api/orchestrationCenter/
src/app/api/memory/
src/app/api/agent/
src/app/api/roomCenter/
src/app/api/sse/
src/app/api/inspectionCenter/
src/app/api/task/
src/app/api/health/
src/app/api/          (if completely empty)
```

### Phase 3: Edit live files

1. **`src/lib/api/room.ts`**: Remove the `createAndParseUserMessage` function
   (~40 lines). Keep all other functions.

2. **`src/lib/auth.ts`**: Remove `getAuthToken` and `getAuthHeaders` functions
   (~10 lines). Keep `setDefaultGetToken`, `getClientAuthHeaders`, and any other
   live functions.

3. **`src/lib/api/index.ts`**: Remove barrel re-export lines for `orchestration` and
   `memory`:

   ```typescript
   // Remove these lines:
   export * from './orchestration'
   export * from './memory'
   ```

### Phase 4: Build verification

```bash
npm run build
```

The build must succeed with zero errors introduced by the cleanup. Pre-existing
warnings or type errors are acceptable (do not fix unrelated issues).

### Phase 5: Test verification

```bash
npm run test
```

All existing tests must pass. If any test imports a deleted file, inspect the test
before removing it:

1. **Test only exercises the deleted module** (e.g., unit tests for `workflow-api.ts`):
   safe to delete — the test is dead code itself.
2. **Test exercises shared logic that the deleted module also used** (e.g., a test for
   a utility function that happens to import a deleted type for test data): refactor
   the test to remove the dead import while keeping the test assertions intact.
3. **Uncertain**: keep the test, remove/stub the dead import, and leave a `// TODO:`
   comment for manual review.

Do NOT blanket-delete any test that touches a deleted file — the test may cover live
code paths that would lose regression protection.

---

## 5. Risk Assessment

| Risk | Likelihood | Mitigation |
|---|---|---|
| A deleted file is actually referenced somewhere | Low (grep verification) | Phase 1 verification catches this. Run grep before every deletion. |
| Legacy API routes are hit by external services | Very low (they proxy to backend; external services call backend directly) | Check server logs for traffic to `/api/*` routes before deleting. If traffic exists, routes are not dead. |
| Bundle size changes are unexpected | Low | Compare bundle size before/after. Dead code is tree-shaken by Next.js for client bundles, but API routes add server-side overhead. |
| `src/app/api/` directory removal breaks Next.js | Very low | Next.js only requires `app/` to exist. Empty subdirectories have no effect. |

---

## 6. Expected Impact

- **~1,677 lines removed** from the codebase.
- **8 fewer API routes** for Next.js to compile and serve.
- **Cleaner imports**: `src/lib/api/index.ts` no longer re-exports dead modules.

**Follow-up check**: After removing dead re-exports from `src/lib/api/index.ts`,
verify that remaining barrel re-exports are not pulling in unnecessary transitive
dependencies. Barrel files can defeat tree-shaking if a re-exported module has
side-effects. Run `npx next build --profile` and compare the `.next/analyze/` output
(if `@next/bundle-analyzer` is configured) before and after. If certain re-exports
cause unexpected bundling, convert them to direct imports at the call site.
- **Reduced developer confusion**: new contributors will not waste time understanding
  the dead workflow subsystem.
- **Smaller server bundle**: API routes add to the server-side bundle even if unused.

---

## 7. Out of Scope

- Refactoring live code that was previously coupled to dead code (e.g., types in
  `src/lib/types/index.ts` that reference `BaseTask`/`MetaTask` — these types may
  still be used by the task center pages).
- Removing backend endpoints that correspond to dead frontend code (backend endpoints
  may have other consumers).
- Adding new tests to replace removed dead code.
- Refactoring `useRoomWebhook.ts` (the "god hook" mentioned in `architecture.md`) —
  that is a separate effort.
