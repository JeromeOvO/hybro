# Room sync refactor

Unified DB hydration and SSE handler decomposition.

## Hydration (`src/lib/room-sync/`)

| Module | Role |
|--------|------|
| `apply-db-messages.ts` | `upsertMany` + `clearByMessageIds(appliedIds)` |
| `hitl-overlay.ts` | Pending HITL overlay + initial “mark resolved” pass |
| `hydrate-room.ts` | `hydrateRoomFromDb({ phase })` orchestrator |
| `types.ts` | `HydrateRoomPhase`: `initial` \| `reconcile` \| `hitl_overlay` |

### Phases

- **`initial`** — fetch → normalize → apply → stamp turn terminal → mark hydrated → HITL overlay + mark resolved from batch
- **`reconcile`** — fetch → normalize → apply → `markDbSynced` → prune processing placeholder
- **`hitl_overlay`** — pending HITL fetch + overlay only (SSE reconnect; no mark-resolved loop)

### Hooks

- `useRoomHydration` — gates (`hydratedFromDb`, `hydrationStartedRef`), reconcile inflight dedupe + zero-count retry
- `useRoomSSEConnection` — reconnect calls `hydrateRoomFromDb({ phase: 'hitl_overlay' })`

## SSE (done)

| Module | Role |
|--------|------|
| `dispatch.ts` | Router: drop gate → buffer gate → per-type handler |
| `correlation.ts` | `TURN_CORRELATED_EVENT_TYPES` (require id); `CORRELATION_BUFFER_EVENT_TYPES` (pending buffer only — excludes HITL, which resolves eagerly) |
| `artifacts.ts` | Shared artifact parsing |
| `apply-commands.ts` | Sync `RoomCommand[]` batch (`task_update` terminal path) |
| `handlers/*.ts` | One file per event family |

`index.ts` re-exports `createSSEDispatcher` only (~5 lines).

See `docs/architecture.md` §15.1.
