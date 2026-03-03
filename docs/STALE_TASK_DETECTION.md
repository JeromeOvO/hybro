# Stale Task Detection — Orphaned Task Recovery

> **Status: Implemented**

---

## 1. Overview

When a user refreshes the page or reconnects after an SSE outage, some task messages may
be stuck in non-terminal states (`submitted`, `working`, `input-required`) because the
terminal SSE event was missed. Stale task detection identifies these orphaned tasks during
DB hydration and marks them as `failed` with an appropriate error message.

Without this system, users would see perpetual "Working..." spinners for tasks that
finished (or crashed) long ago.

---

## 2. Architecture

### Detection Logic

`detectAndMarkStaleTasks(messages)` processes an array of `IncomingMessage` objects and
returns a new array with stale tasks rewritten to `failed` status.

For each message, the function applies the following rules:

1. **Skip non-agent messages**: Only processes messages with `messageType === 'agent'`.
2. **Skip terminal tasks**: Messages with `taskStatus` in `completed`, `failed`, `canceled`, `rejected` are already resolved.
3. **Skip answered HITL**: If `hitlUserAnswer` is defined, the request was already responded to.
4. **Interactive states** (`input-required`, `auth-required`):
   - If `hitlExpiresAt` exists: compare against current time. If not expired, keep as-is.
   - If no `hitlExpiresAt`: use `taskUpdatedAt || timestamp` + 24-hour fallback threshold (matches backend HITL expiry default).
   - If expired: rewrite to `taskStatus: 'failed'`, `taskError: 'Request expired — no response was received before the deadline'`.
5. **Non-interactive non-terminal** (`submitted`, `working`):
   - Use `taskUpdatedAt || timestamp` + 10-minute threshold (matches backend task timeout).
   - If stale: rewrite to `taskStatus: 'failed'`, `taskError: 'Task timed out — no updates received within the expected timeframe'`.

### Thresholds

| State Type | Threshold | Rationale |
|---|---|---|
| `working` / `submitted` | 10 minutes | Matches backend task execution timeout |
| `input-required` / `auth-required` (no explicit expiry) | 24 hours | Matches backend HITL expiry default |
| `input-required` / `auth-required` (with `hitlExpiresAt`) | Exact expiry time | Uses backend-provided deadline |

### Integration Points

`detectAndMarkStaleTasks` is called in two places:

1. **`hydrateFromDb()`** — on initial room load, after fetching messages from the API.
2. **`reconcileWithDb()`** — after SSE reconnection, during gap recovery.

Both call the function before `filterHydrationMessages()` and `upsertMany()`, so stale
tasks are marked before entering the normalized store.

---

## 3. Key Design Decisions

- **Client-side detection over server-side**: Stale detection runs entirely in the browser rather than relying on a backend endpoint. This keeps the detection independent of backend availability and avoids an extra API round-trip on page load. The tradeoff is that client clock skew can cause false positives.
- **Thresholds mirror backend defaults**: The 10-minute task timeout and 24-hour HITL expiry match the backend's own configuration. This coupling is intentional — it avoids marking tasks as stale while the backend still considers them active. The risk is that threshold changes on the backend require a corresponding frontend update.
- **Pure function, no side effects**: `detectAndMarkStaleTasks` is a pure function that takes and returns an array. It does not mutate the store directly, making it testable in isolation and composable with the hydration/reconciliation pipeline.
- **Rewrite to `failed`, not removal**: Stale tasks are rewritten with `taskStatus: 'failed'` and a descriptive `taskError` rather than being removed from the message list. This preserves the message timeline and gives users context about what happened.

---

## 4. Code References

| Concept | File | Notes |
|---|---|---|
| Detection function | `src/stores/message-store/stale-detection.ts` | `detectAndMarkStaleTasks()` — main export |
| Threshold constants | `src/stores/message-store/stale-detection.ts` (lines 5-6) | 10min for tasks, 24h for interactive |
| Tests | `src/stores/message-store/__tests__/stale-detection.test.ts` | Edge cases for all state types and thresholds |
| Hydration integration | `src/hooks/useRoomWebhook.ts` | Called in `hydrateFromDb()` |
| Reconciliation integration | `src/hooks/useRoomWebhook.ts` | Called in `reconcileWithDb()` |

---

## 5. Known Limitations

- **Client-side only**: Detection runs in the browser using `Date.now()`. If the user's system clock is significantly wrong, tasks may be incorrectly marked as stale or missed.
- **No backend coordination**: The stale marking is a local UI decision. The backend may still consider the task active. There is no API call to confirm or report stale task detection.
- **Irreversible local marking**: Once marked as `failed` locally, a stale task stays failed even if the backend later delivers a completion event (the SSE source priority would override it, but only if the user is still connected).
- **Threshold mismatch risk**: If the backend changes its task timeout or HITL expiry defaults, the frontend thresholds must be updated manually.
