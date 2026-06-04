# Room SSE Final Wire Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt the room frontend to the backend final SSE wire format and final send-message routing payload, with no compatibility for legacy SSE outer protocols or `target_group`.

**Architecture:** Treat the SSE envelope as `SSEFrame<T, D>` with exactly four top-level keys: `type`, `timestamp`, `room_id`, and `data`. The low-level stream reader only extracts `data:` blocks and `JSON.parse`s them; room dispatch uses top-level `frame.type`, ignores unknown types with debug logging, and never reads named SSE `event:` metadata. Send-message routing becomes mutually exclusive between `mentioned_agent_ids` and canonical `message_target_mode` fields. The generated `client_request_id` is the turn anchor: it is stored on the optimistic user message, sent to the backend, mapped to the server `message_id`, and reused by follow-up SSE events to attach task, artifact, HITL, processing, and response messages to the correct user turn.

**Tech Stack:** Next.js 16, React 19, TypeScript, Zustand stores, Vitest, MSW, fetch-based SSE reader in `src/lib/api/sse.ts`.

**Backend Cutover Prerequisite:** Execute this frontend migration only with the backend final SSE publisher that emits `connected` and `heartbeat` in the final envelope with `data` (`connected.data.connection_id`, `heartbeat.data = {}`). Do not add frontend compatibility for the old connected/heartbeat shapes.

---

## File Structure

- Modify `src/lib/types/sse.ts`: replace loose optional `SSEMessage.data` with final `SSEFrame`/`RoomSSEType` data model, add runtime type guards, add `queued` processing status, remove unsupported `user_message` and `turn_event` from the known union.
- Modify `src/lib/api/sse.ts`: forward every parsed frame including `connected` and `heartbeat`, keep ignoring named SSE lines, and type `onMessage` as a parsed frame rather than a legacy message.
- Modify `src/hooks/useRoomSSE.ts` and `src/hooks/room/useRoomSSEConnection.ts`: propagate the new frame type through hooks.
- Modify `src/hooks/room/sse-handlers/dispatch.ts`: dispatch by top-level `frame.type`; add explicit no-op/debug handlers for `connected`, `cancellation`, `hub_agent_event`, and `debate_round`; remove `user_message` and `turn_event` cases.
- Modify `src/hooks/room/sse-handlers/correlation.ts`: remove the old uncorrelated compat fallback and resolve turn correlation from `client_request_id`, `message_id`, and `related_message_id` only; `client_request_id` is the primary anchor for the optimistic user message and all subsequent turn events.
- Modify `src/hooks/room/sse-handlers/handlers/misc.ts`: update misc handlers for final events and object-shaped error/processing details.
- Modify `src/hooks/room/sse-handlers/handlers/processing-status.ts` and `src/hooks/room/processing-status-log.ts`: handle `queued`, `processing`, `awaiting_input`, terminal statuses, and `details: Record<string, unknown> | null` without string compatibility.
- Modify `src/hooks/room/sse-handlers/handlers/agent-response.ts`: add `agent_response_partial` streaming behavior and keep final `agent_response` as the persistence/finalization signal.
- Modify `src/hooks/room/sse-handlers/handlers/task-submitted.ts`, `task-update.ts`, `artifact-update.ts`, and `hitl.ts`: consume required `data` and final field optionality from the new event-specific types.
- Modify `src/lib/api/room.ts`: build the final `SendMessagePayload`, always include `client_request_id`, never include `target_group`, and enforce mention-vs-target mutual exclusivity.
- Modify `src/lib/types/request.ts` and `src/lib/types/agent-group.ts`: add final send-message request types and remove or deprecate legacy routing assumptions from the send path.
- Modify `src/components/room-chat-input.tsx`, `src/components/composer/ComposerShell.tsx`, `src/components/room-page-shell.tsx`, `src/app/c/chat/page.tsx`, `src/app/c/room/[id]/page.tsx`, and `src/stores/room-ui-store.ts`: pass explicit mention routing or canonical non-mention dispatch through the create-room handoff and room send flow, so saved-group autosend preserves `target_group_id`.
- Update tests in `tests/unit/lib/sse-connection.test.ts`, `tests/unit/hooks/useRoomSSE.test.ts`, `tests/unit/hooks/useRoomWebhook.test.ts`, `tests/unit/lib/room-api.test.ts`, `tests/unit/components/room-chat-input-mention.test.tsx`, `tests/unit/components/room-page-prefill.test.tsx`, `tests/unit/hooks/useChatRoomCreation.test.ts`, and `tests/unit/stores/room-ui-store.test.ts`.

## Task 1: Define the Final SSE Contract

**Files:**
- Modify: `src/lib/types/sse.ts`
- Create: `tests/unit/lib/sse-types.test.ts`

- [ ] **Step 1: Write failing tests for known type guards and required envelope data**

Add `tests/unit/lib/sse-types.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { isConnectedData, isRoomSSEType, hasSSEFrameEnvelope, PROCESSING_STATUS } from '@/lib/types/sse'

describe('final room SSE types', () => {
  it('recognizes only final room SSE top-level types', () => {
    expect(isRoomSSEType('connected')).toBe(true)
    expect(isRoomSSEType('heartbeat')).toBe(true)
    expect(isRoomSSEType('agent_response_partial')).toBe(true)
    expect(isRoomSSEType('hub_agent_event')).toBe(true)
    expect(isRoomSSEType('debate_round')).toBe(true)
    expect(isRoomSSEType('user_message')).toBe(false)
    expect(isRoomSSEType('turn_event')).toBe(false)
    expect(isRoomSSEType('event' + '_type')).toBe(false)
  })

  it('requires the final top-level SSE envelope including data', () => {
    expect(hasSSEFrameEnvelope({
      type: 'heartbeat',
      timestamp: '2026-06-04T00:00:00.000Z',
      room_id: 'room-1',
      data: {},
    })).toBe(true)

    expect(hasSSEFrameEnvelope({
      type: 'heartbeat',
      timestamp: '2026-06-04T00:00:00.000Z',
      room_id: 'room-1',
    })).toBe(false)
  })

  it('rejects legacy outer protocol fields at the top level', () => {
    const legacyEventTypeKey = 'event' + '_type'
    expect(hasSSEFrameEnvelope({
      type: 'heartbeat',
      timestamp: '2026-06-04T00:00:00.000Z',
      room_id: 'room-1',
      data: {},
      [legacyEventTypeKey]: 'heartbeat',
    })).toBe(false)

    const legacyPayloadKey = 'pay' + 'load'
    expect(hasSSEFrameEnvelope({
      type: 'run_event',
      timestamp: '2026-06-04T00:00:00.000Z',
      room_id: 'room-1',
      data: {},
      [legacyPayloadKey]: {},
    })).toBe(false)

    const legacyRoutingKey = 'target' + '_group'
    expect(hasSSEFrameEnvelope({
      type: 'task_submitted',
      timestamp: '2026-06-04T00:00:00.000Z',
      room_id: 'room-1',
      data: {},
      [legacyRoutingKey]: 'all_agents',
    })).toBe(false)

    expect(hasSSEFrameEnvelope({
      type: 'run_event',
      timestamp: '2026-06-04T00:00:00.000Z',
      room_id: 'room-1',
      data: { payload: {} },
    })).toBe(true)
  })

  it('includes queued in processing statuses', () => {
    expect(PROCESSING_STATUS.QUEUED).toBe('queued')
  })

  it('requires connected.data.connection_id', () => {
    expect(isConnectedData({ connection_id: 'conn-1' })).toBe(true)
    expect(isConnectedData({})).toBe(false)
    expect(isConnectedData({ connection_id: '' })).toBe(false)
  })
})
```

Run: `npx vitest run tests/unit/lib/sse-types.test.ts`

Expected: FAIL because the guards and `PROCESSING_STATUS.QUEUED` do not exist yet.

- [ ] **Step 2: Replace the loose SSE message shape with final discriminated frame types**

In `src/lib/types/sse.ts`, replace the current `SSEMessage` interface block with these final contract types, then keep the existing A2A `TaskState` exports below it:

```ts
export type SSEFrame<T extends string, D> = {
  type: T
  timestamp: string
  room_id: string
  data: D
}

export type RoomSSEType =
  | 'connected'
  | 'heartbeat'
  | 'processing_status'
  | 'run_event'
  | 'task_submitted'
  | 'task_update'
  | 'artifact_update'
  | 'agent_response'
  | 'agent_response_partial'
  | 'error'
  | 'hitl_input_requested'
  | 'hitl_status_update'
  | 'cancellation'
  | 'hub_agent_event'
  | 'debate_round'

export const ROOM_SSE_TYPES = [
  'connected',
  'heartbeat',
  'processing_status',
  'run_event',
  'task_submitted',
  'task_update',
  'artifact_update',
  'agent_response',
  'agent_response_partial',
  'error',
  'hitl_input_requested',
  'hitl_status_update',
  'cancellation',
  'hub_agent_event',
  'debate_round',
] as const satisfies readonly RoomSSEType[]

const ROOM_SSE_TYPE_SET = new Set<string>(ROOM_SSE_TYPES)

export type ConnectedData = { connection_id: string }
export type HeartbeatData = Record<string, never>

export type ProcessingStatus =
  | 'queued'
  | 'processing'
  | 'awaiting_input'
  | 'completed'
  | 'failed'
  | 'canceled'
  | 'rejected'
  | 'rate_limited'
  | 'error'

export type ProcessingStatusData = {
  message_id: string | null
  client_request_id: string
  status: ProcessingStatus
  details: Record<string, unknown> | null
  agent_id?: string
  agents?: Array<Record<string, unknown>>
}

export type RunEventData = {
  event_id: string
  run_id: string
  seq: number
  type: string
  payload: Record<string, unknown>
  correlation_id: string | null
}

export type TaskSubmittedData = {
  message_id: string
  task_id: string
  agent_name: string
  agent_id: string | null
  status: string
  related_message_id: string | null
  step_number?: number | null
  total_steps?: number | null
  task_content?: string | null
  client_request_id: string
}

export type TaskUpdateData = {
  message_id: string
  status: string
  content?: string | null
  error?: string | null
  requires_input?: boolean
  requires_auth?: boolean
  status_message?: string | null
  agent_name?: string | null
  agent_id?: string | null
  related_message_id?: string | null
  step_number?: number | null
  total_steps?: number | null
  task_content?: string | null
  parts?: Array<Record<string, unknown>>
  client_request_id: string
}

export type ArtifactUpdateData = {
  message_id: string
  agent_id: string
  artifact: unknown
  append: boolean
  last_chunk: boolean
  client_request_id: string
}

export type AgentResponseData = {
  message_id: string
  agent_id: string
  related_message_id?: string | null
  content?: string
  parts?: Array<Record<string, unknown>>
  client_request_id: string
}

export type AgentResponsePartialData = {
  message_id: string
  agent_id: string
  related_message_id?: string | null
  content_delta: string
  client_request_id: string
}

export type GlobalErrorData = {
  error: string
  error_type?: string
  retry_after_seconds?: number | null
  user_requests_used?: number
  user_requests_limit?: number
  system_requests_used?: number
  system_requests_limit?: number
  message_id?: never
  agent_id?: never
  client_request_id?: never
}

export type TurnErrorData = {
  error: string
  error_type?: string
  message_id?: string | null
  agent_id?: string | null
  retry_after_seconds?: number | null
  user_requests_used?: number
  user_requests_limit?: number
  system_requests_used?: number
  system_requests_limit?: number
  client_request_id: string
}

export type ErrorData = GlobalErrorData | TurnErrorData

export type HITLInputRequestedData = {
  request_id: string
  message_id: string
  related_message_id?: string | null
  source: string
  prompt: string
  prompt_type: string
  choices?: unknown
  agent_id?: string | null
  agent_name?: string | null
  source_step_id?: string | null
  group_id?: string
  group_total?: number
  group_index?: number
  client_request_id: string
}

export type HITLStatusUpdateData = {
  request_id: string
  message_id: string
  related_message_id?: string | null
  source: string
  status: string
  error_message?: string
  agent_id?: string | null
  agent_name?: string | null
  source_step_id?: string | null
  group_id?: string
  group_total?: number
  group_index?: number
  client_request_id: string
}

export type GenericRoomEventData = Record<string, unknown>

export type RoomSSEFrameMap = {
  connected: SSEFrame<'connected', ConnectedData>
  heartbeat: SSEFrame<'heartbeat', HeartbeatData>
  processing_status: SSEFrame<'processing_status', ProcessingStatusData>
  run_event: SSEFrame<'run_event', RunEventData>
  task_submitted: SSEFrame<'task_submitted', TaskSubmittedData>
  task_update: SSEFrame<'task_update', TaskUpdateData>
  artifact_update: SSEFrame<'artifact_update', ArtifactUpdateData>
  agent_response: SSEFrame<'agent_response', AgentResponseData>
  agent_response_partial: SSEFrame<'agent_response_partial', AgentResponsePartialData>
  error: SSEFrame<'error', ErrorData>
  hitl_input_requested: SSEFrame<'hitl_input_requested', HITLInputRequestedData>
  hitl_status_update: SSEFrame<'hitl_status_update', HITLStatusUpdateData>
  cancellation: SSEFrame<'cancellation', GenericRoomEventData>
  hub_agent_event: SSEFrame<'hub_agent_event', GenericRoomEventData>
  debate_round: SSEFrame<'debate_round', GenericRoomEventData>
}

export type RoomSSEMessage = RoomSSEFrameMap[RoomSSEType]
export type AnySSEFrame = SSEFrame<string, unknown>
export type SSEMessage = RoomSSEMessage

export function isRoomSSEType(value: string): value is RoomSSEType {
  return ROOM_SSE_TYPE_SET.has(value)
}

export function isConnectedData(value: unknown): value is ConnectedData {
  return Boolean(
    value &&
    typeof value === 'object' &&
    typeof (value as { connection_id?: unknown }).connection_id === 'string' &&
    (value as { connection_id: string }).connection_id.length > 0
  )
}

export function hasSSEFrameEnvelope(value: unknown): value is AnySSEFrame {
  if (!value || typeof value !== 'object') return false
  const frame = value as Record<string, unknown>
  const keys = Object.keys(frame).sort()
  return (
    keys.length === 4 &&
    keys[0] === 'data' &&
    keys[1] === 'room_id' &&
    keys[2] === 'timestamp' &&
    keys[3] === 'type' &&
    typeof frame.type === 'string' &&
    typeof frame.timestamp === 'string' &&
    typeof frame.room_id === 'string' &&
    Object.prototype.hasOwnProperty.call(frame, 'data')
  )
}
```

- [ ] **Step 3: Replace the existing internal processing status section**

Replace the entire existing lower `// --- Internal Processing Status (SSE processing_status events) ---` section, including the old `ProcessingStatus` type, `PROCESSING_STATUS`, `PROCESSING_DONE_STATUSES`, and `isProcessingDone`, with:

```ts
export const PROCESSING_STATUS = {
  QUEUED: 'queued',
  PROCESSING: 'processing',
  AWAITING_INPUT: 'awaiting_input',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELED: 'canceled',
  REJECTED: 'rejected',
  RATE_LIMITED: 'rate_limited',
  ERROR: 'error',
} as const satisfies Record<string, ProcessingStatus>

export const PROCESSING_DONE_STATUSES: ProcessingStatus[] = [
  PROCESSING_STATUS.COMPLETED,
  PROCESSING_STATUS.CANCELED,
  PROCESSING_STATUS.FAILED,
  PROCESSING_STATUS.REJECTED,
  PROCESSING_STATUS.RATE_LIMITED,
  PROCESSING_STATUS.ERROR,
]

export function isProcessingDone(status: ProcessingStatus): boolean {
  return PROCESSING_DONE_STATUSES.includes(status)
}
```

Run: `npx vitest run tests/unit/lib/sse-types.test.ts`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/lib/types/sse.ts tests/unit/lib/sse-types.test.ts
git commit -m "refactor: define final room sse frame types"
```

## Task 2: Update the Low-Level SSE Reader

**Files:**
- Modify: `src/lib/api/sse.ts`
- Modify: `tests/unit/lib/sse-connection.test.ts`

- [ ] **Step 1: Write failing reader tests**

In `tests/unit/lib/sse-connection.test.ts`, update the heartbeat test and add a named-event regression:

```ts
it('should forward heartbeat frames because heartbeat has final data payload', async () => {
  const onMessage = vi.fn()
  const { instance } = await connectAndOpen({ onMessage })

  const heartbeat = {
    type: 'heartbeat',
    room_id: 'test-room',
    timestamp: new Date().toISOString(),
    data: {},
  }
  instance.simulateMessage(heartbeat)

  await vi.advanceTimersByTimeAsync(0)

  expect(onMessage).toHaveBeenCalledWith(heartbeat)
})

it('should ignore named SSE event metadata and use only parsed data.type', async () => {
  const onMessage = vi.fn()
  const { instance } = await connectAndOpen({ onMessage })

  const frame = {
    type: 'heartbeat',
    room_id: 'test-room',
    timestamp: new Date().toISOString(),
    data: {},
  }
  instance.simulateRawData(`event: processing_status\ndata: ${JSON.stringify(frame)}\n\n`)

  await vi.advanceTimersByTimeAsync(0)

  expect(onMessage).toHaveBeenCalledWith(frame)
})
```

Run: `npx vitest run tests/unit/lib/sse-connection.test.ts`

Expected: FAIL because `SSEConnection` currently suppresses heartbeat frames.

- [ ] **Step 2: Stop suppressing heartbeat and validate only the envelope**

In `src/lib/api/sse.ts`:

```ts
import type { AnySSEFrame, SSEConnectionStatus } from '@/lib/types/sse'
import { hasSSEFrameEnvelope } from '@/lib/types/sse'
```

Change `SSEConnectionOptions.onMessage`:

```ts
onMessage?: (message: AnySSEFrame) => void
```

Replace the parse block inside `readStream` with:

```ts
const parsed: unknown = JSON.parse(data)

if (!hasSSEFrameEnvelope(parsed)) {
  console.debug('Ignoring SSE payload without final frame envelope:', parsed)
  continue
}

this.options.onMessage?.(parsed)
```

Do not add any branch that checks `message.type === 'heartbeat'`; heartbeat is now a normal frame.

- [ ] **Step 3: Keep named SSE metadata ignored**

Leave `processSSEBuffer` behavior as data-only extraction. It should continue ignoring `event:`, `id:`, `retry:`, and comment lines. The named-event regression in Step 1 proves frontend dispatch does not use `event.type`.

- [ ] **Step 4: Run tests**

Run: `npx vitest run tests/unit/lib/sse-connection.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/api/sse.ts tests/unit/lib/sse-connection.test.ts
git commit -m "refactor: forward final sse frames from stream reader"
```

## Task 3: Dispatch Only by Top-Level `frame.type`

**Files:**
- Modify: `src/hooks/useRoomSSE.ts`
- Modify: `src/hooks/room/useRoomSSEConnection.ts`
- Modify: `src/hooks/room/sse-handlers/dispatch.ts`
- Modify: `src/hooks/room/sse-handlers/correlation.ts`
- Modify: `src/hooks/room/sse-handlers/pending-turn-buffer.ts`
- Modify: `tests/unit/hooks/useRoomSSE.test.ts`
- Modify: `tests/unit/hooks/useRoomWebhook.test.ts`

- [ ] **Step 1: Write failing dispatcher tests**

In `tests/unit/hooks/useRoomWebhook.test.ts`, replace the legacy `user_message` tests with final unknown/connected/heartbeat coverage:

```ts
it('ignores unknown final-frame type without crashing', async () => {
  const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
  await mountHook()

  await act(async () => {
    await capturedOnMessage!({
      type: 'new_backend_type',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: { anything: true },
    })
  })

  expect(useMessageStore.getState().orderedIds).toEqual([])
  expect(debugSpy).toHaveBeenCalled()
  debugSpy.mockRestore()
})

it('handles connected and heartbeat frames without message-store writes', async () => {
  await mountHook()

  await act(async () => {
    await capturedOnMessage!({
      type: 'connected',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: { connection_id: 'conn-1' },
    })
    await capturedOnMessage!({
      type: 'heartbeat',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {},
    })
  })

  expect(useMessageStore.getState().orderedIds).toEqual([])
})

it('accepts final no-op event types without crashing or writing messages', async () => {
  const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
  await mountHook()

  await act(async () => {
    await capturedOnMessage!({
      type: 'cancellation',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: { reason: 'user_cancelled' },
    })
    await capturedOnMessage!({
      type: 'hub_agent_event',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: { agent_id: 'agent-1', event: 'observed' },
    })
    await capturedOnMessage!({
      type: 'debate_round',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: { round: 1 },
    })
  })

  expect(useMessageStore.getState().orderedIds).toEqual([])
  expect(debugSpy).toHaveBeenCalled()
  debugSpy.mockRestore()
})
```

Run: `npx vitest run tests/unit/hooks/useRoomWebhook.test.ts`

Expected: FAIL because the mocked callback is typed as `SSEMessage`, unknown types are not accepted, and `connected`/final no-op types have no explicit dispatch cases.

- [ ] **Step 2: Propagate `AnySSEFrame` through hooks**

In `src/hooks/useRoomSSE.ts`, import and use `AnySSEFrame`:

```ts
import type { AnySSEFrame } from '@/lib/types/sse'

interface UseRoomSSEOptions {
  roomId: string
  enabled?: boolean
  getToken?: () => Promise<string | null>
  onMessage?: (message: AnySSEFrame) => void
  onConnectionChange?: (connected: boolean) => void
}
```

Update `handleMessage` to accept `AnySSEFrame`.

In `src/hooks/room/useRoomSSEConnection.ts`, change `handleSSEMessage` to:

```ts
handleSSEMessage: (message: AnySSEFrame) => void
```

- [ ] **Step 3: Remove legacy event cases from dispatcher**

In `src/hooks/room/sse-handlers/dispatch.ts`:

```ts
import type { AnySSEFrame, RoomSSEFrameMap, RoomSSEMessage, RoomSSEType } from '@/lib/types/sse'
import { isRoomSSEType, ROOM_SSE_TYPES } from '@/lib/types/sse'
import type { CorrelationResult } from './correlation'
import type { SSEHandlerDeps } from './types'
```

Change the returned function to accept `AnySSEFrame`. At the top of the function:

```ts
if (!isRoomSSEType(sseMessage.type)) {
  console.debug('Ignoring unknown room SSE frame type:', sseMessage.type, sseMessage)
  return
}

const roomMessage = sseMessage as RoomSSEMessage
```

Add an exhaustive known-type coverage object next to the dispatcher. This makes a newly added `RoomSSEType` fail typecheck until it is explicitly handled or no-op handled:

```ts
export const HANDLED_ROOM_SSE_TYPES = {
  connected: true,
  heartbeat: true,
  processing_status: true,
  run_event: true,
  task_submitted: true,
  task_update: true,
  artifact_update: true,
  agent_response: true,
  agent_response_partial: true,
  error: true,
  hitl_input_requested: true,
  hitl_status_update: true,
  cancellation: true,
  hub_agent_event: true,
  debate_round: true,
} satisfies Record<RoomSSEType, true>
```

Use `roomMessage` for correlation and switch. Remove imports and cases for `handleUserMessage` and `handleTurnEvent`.

Add minimal typed no-op stubs for any new handlers referenced by the dispatcher in this task; Task 4 will fill in their final behavior:

```ts
function handleConnected(sseMessage: RoomSSEFrameMap['connected']): void {
  console.debug('Room SSE connected:', sseMessage.data.connection_id)
}

function handleCancellation(_deps: SSEHandlerDeps, sseMessage: RoomSSEFrameMap['cancellation']): void {
  console.debug('Room SSE cancellation event:', sseMessage.data)
}

function handleHubAgentEvent(sseMessage: RoomSSEFrameMap['hub_agent_event']): void {
  console.debug('Room SSE hub_agent_event:', sseMessage.data)
}

function handleDebateRound(sseMessage: RoomSSEFrameMap['debate_round']): void {
  console.debug('Room SSE debate_round:', sseMessage.data)
}

function handleAgentResponsePartial(
  _deps: SSEHandlerDeps,
  sseMessage: RoomSSEFrameMap['agent_response_partial'],
  _correlation: CorrelationResult,
): void {
  console.debug('Room SSE partial agent response pending streaming handler:', sseMessage.data.message_id)
}
```

Add switch cases:

```ts
case 'connected':
  handleConnected(roomMessage)
  break
case 'heartbeat':
  handleHeartbeat()
  break
case 'agent_response_partial':
  handleAgentResponsePartial(deps, roomMessage, correlation)
  break
case 'cancellation':
  handleCancellation(deps, roomMessage)
  break
case 'hub_agent_event':
  handleHubAgentEvent(roomMessage)
  break
case 'debate_round':
  handleDebateRound(roomMessage)
  break
```

Keep default as a debug fallback:

```ts
default:
  console.debug('Ignoring unhandled room SSE frame type:', roomMessage.type, roomMessage)
```

Add a type/list regression:

```ts
expect(Object.keys(HANDLED_ROOM_SSE_TYPES).sort()).toEqual([...ROOM_SSE_TYPES].sort())
```

- [ ] **Step 4: Update correlation to final rules**

In `src/hooks/room/sse-handlers/correlation.ts`, remove `ENABLE_UNCORRELATED_SSE_COMPAT_FALLBACK`. Update `resolveSseCorrelation` so it:

```ts
const clientReqId = sseMessage.data && typeof sseMessage.data === 'object'
  ? (sseMessage.data as { client_request_id?: string }).client_request_id
  : undefined
const messageId = sseMessage.data && typeof sseMessage.data === 'object'
  ? (sseMessage.data as { message_id?: string | null }).message_id ?? undefined
  : undefined
const relatedMessageId = sseMessage.data && typeof sseMessage.data === 'object'
  ? (sseMessage.data as { related_message_id?: string | null }).related_message_id ?? undefined
  : undefined
```

Then resolve in this order:

1. If a turn-correlated event has no `client_request_id`, debug-log and drop it even if it has `message_id` or `related_message_id`.
2. If `client_request_id` already maps to a message id, apply immediately.
3. Do not create a `client_request_id -> user message_id` mapping from SSE child event fields. The mapping is created by `useSendMessage` from the `SendMessage` response `message_id`; `resolveSseCorrelation` only reads that mapping.
4. Use child `message_id` values only as emitted message ids for store writes after the `client_request_id` anchor has resolved; never let child `message_id` or `related_message_id` replace `client_request_id`.
5. If `client_request_id` exists but no message is resolved yet, buffer only the types in `CORRELATION_BUFFER_EVENT_TYPES`.

This removes `NEXT_PUBLIC_SSE_CORRELATION_COMPAT` from the room SSE path.

Remove any handler-level `resolveClientRequestMessageId(clientReqId, childMessageId)` calls that map the anchor to task, agent-response, artifact, or HITL child message ids. In particular, `src/hooks/room/sse-handlers/handlers/hitl.ts` must not resolve the anchor to the HITL child `message_id` or `related_message_id`; it may use `related_message_id` only to validate the already-resolved user message id.

Also update `TURN_CORRELATED_EVENT_TYPES` and `CORRELATION_BUFFER_EVENT_TYPES` so final and partial agent responses plus HITL events are anchored by `client_request_id`, and can be buffered when they arrive before the user message id has been resolved:

```ts
export const TURN_CORRELATED_EVENT_TYPES = new Set<RoomSSEType>([
  'processing_status',
  'task_submitted',
  'task_update',
  'artifact_update',
  'agent_response',
  'agent_response_partial',
  'hitl_input_requested',
  'hitl_status_update',
])

export const CORRELATION_BUFFER_EVENT_TYPES = new Set<RoomSSEType>([
  'processing_status',
  'task_submitted',
  'task_update',
  'artifact_update',
  'agent_response',
  'agent_response_partial',
  'hitl_input_requested',
  'hitl_status_update',
])
```

Do not add `error` to the generic correlation sets because global errors are valid without turn identifiers. `handleError` performs its own runtime split: global errors have no turn identifiers and may omit `client_request_id`; turn-scoped errors include `message_id`, `agent_id`, or `client_request_id` and must have `client_request_id`.

Before adding the regressions below, make the test helpers explicit:

```ts
import type { RoomSSEMessage } from '@/lib/types/sse'
import {
  flushPendingSseEvents,
  getResolvedMessageId,
  resolveClientRequestMessageId,
} from '@/hooks/room/sse-handlers/pending-turn-buffer'
```

If any of these helpers are currently module-local, export them from `src/hooks/room/sse-handlers/pending-turn-buffer.ts` rather than reimplementing test-only copies. The tests must exercise the same production mapping and flushing code that handles early SSE events.

Add a regression to `tests/unit/hooks/useRoomWebhook.test.ts`:

```ts
it('drops turn-correlated events that have message_id but no client_request_id', async () => {
  const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
  await mountHook()

  await act(async () => {
    await capturedOnMessage!({
      type: 'task_update',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'task-without-client-request',
        status: 'working',
      },
    })
  })

  expect(useMessageStore.getState().entities['task-without-client-request']).toBeUndefined()
  expect(debugSpy).toHaveBeenCalledWith(
    'Dropping turn-correlated SSE event without client_request_id:',
    'task_update',
  )
  debugSpy.mockRestore()
})

it('drops final agent_response without client_request_id even when message_id is present', async () => {
  const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
  await mountHook()

  await act(async () => {
    await capturedOnMessage!({
      type: 'agent_response',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'agent-response-without-client-request',
        agent_id: 'agent-1',
        content: 'Should not attach without turn anchor',
      },
    })
  })

  expect(useMessageStore.getState().entities['agent-response-without-client-request']).toBeUndefined()
  expect(debugSpy).toHaveBeenCalledWith(
    'Dropping turn-correlated SSE event without client_request_id:',
    'agent_response',
  )
  debugSpy.mockRestore()
})

it('does not resolve client_request_id to a HITL child message id', async () => {
  await mountHook()
  resolveClientRequestMessageId('req-hitl-child-guard', 'user-msg-1')

  await act(async () => {
    await capturedOnMessage!({
      type: 'hitl_input_requested',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        request_id: 'hitl-1',
        message_id: 'hitl-child-msg-1',
        related_message_id: 'user-msg-1',
        source: 'agent',
        prompt: 'Need input',
        prompt_type: 'text',
        client_request_id: 'req-hitl-child-guard',
      },
    })
  })

  expect(getResolvedMessageId('req-hitl-child-guard')).toBe('user-msg-1')
})

it('does not create a client_request_id mapping from an unresolved HITL child message id', async () => {
  await mountHook()

  await act(async () => {
    await capturedOnMessage!({
      type: 'hitl_input_requested',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        request_id: 'hitl-unresolved-1',
        message_id: 'hitl-child-without-user-mapping',
        source: 'agent',
        prompt: 'Need input',
        prompt_type: 'text',
        client_request_id: 'req-unresolved-hitl-child',
      },
    })
  })

  expect(getResolvedMessageId('req-unresolved-hitl-child')).toBeUndefined()
  expect(useMessageStore.getState().entities['hitl-child-without-user-mapping']).toBeUndefined()
})

it('buffers HITL input by client_request_id until the user message id is resolved', async () => {
  await mountHook()

  await act(async () => {
    await capturedOnMessage!({
      type: 'hitl_input_requested',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        request_id: 'hitl-buffered-1',
        message_id: 'hitl-child-buffered-1',
        related_message_id: 'user-msg-1',
        source: 'agent',
        prompt: 'Need approval',
        prompt_type: 'confirmation',
        client_request_id: 'req-hitl-buffered',
      },
    })
  })

  expect(useMessageStore.getState().entities['hitl-child-buffered-1']).toBeUndefined()
  expect(getResolvedMessageId('req-hitl-buffered')).toBeUndefined()

  resolveClientRequestMessageId('req-hitl-buffered', 'user-msg-1')
  await flushPendingSseEvents('req-hitl-buffered', capturedOnMessage!, 'user-msg-1')

  expect(getResolvedMessageId('req-hitl-buffered')).toBe('user-msg-1')
  expect(useMessageStore.getState().entities['hitl-child-buffered-1']).toBeDefined()
})

For the following early-buffer regressions, add a small `seedServerUserMessage({ id, clientRequestId })` test helper that calls the existing `useMessageStore.getState().upsertMessage(...)` API with a user message for `room-1`.

```ts
it.each([
  {
    label: 'processing_status',
    frame: {
      type: 'processing_status',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'user-msg-1',
        client_request_id: 'req-buffer-processing',
        status: 'processing',
        details: { message: 'Early processing' },
      },
    },
    clientRequestId: 'req-buffer-processing',
    assertBefore: () => {
      expect(useMessageStore.getState().entities['user-msg-1']).toBeUndefined()
    },
    assertAfter: () => {
      expect(useMessageStore.getState().entities['user-msg-1']?.processingStatus).toBe('processing')
    },
  },
  {
    label: 'task_submitted',
    frame: {
      type: 'task_submitted',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'task-submitted-buffered-1',
        task_id: 'task-1',
        agent_name: 'Agent',
        agent_id: 'agent-1',
        status: 'submitted',
        related_message_id: 'user-msg-1',
        client_request_id: 'req-buffer-task-submitted',
      },
    },
    clientRequestId: 'req-buffer-task-submitted',
    assertBefore: () => {
      expect(useMessageStore.getState().entities['task-submitted-buffered-1']).toBeUndefined()
    },
    assertAfter: () => {
      expect(useMessageStore.getState().entities['task-submitted-buffered-1']).toBeDefined()
    },
  },
  {
    label: 'task_update',
    frame: {
      type: 'task_update',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'task-buffered-1',
        client_request_id: 'req-buffer-task-update',
        status: 'working',
        content: 'Early task update',
      },
    },
    clientRequestId: 'req-buffer-task-update',
    assertBefore: () => {
      expect(useMessageStore.getState().entities['task-buffered-1']).toBeUndefined()
    },
    assertAfter: () => {
      expect(useMessageStore.getState().entities['task-buffered-1']).toBeDefined()
    },
  },
  {
    label: 'artifact_update',
    frame: {
      type: 'artifact_update',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'artifact-buffered-1',
        agent_id: 'agent-1',
        client_request_id: 'req-buffer-artifact',
        artifact: { kind: 'text', text: 'Early artifact' },
        append: false,
        last_chunk: true,
      },
    },
    clientRequestId: 'req-buffer-artifact',
    assertBefore: () => {
      expect(useMessageStore.getState().entities['artifact-buffered-1']).toBeUndefined()
    },
    assertAfter: () => {
      expect(useMessageStore.getState().entities['artifact-buffered-1']).toBeDefined()
    },
  },
  {
    label: 'agent_response',
    frame: {
      type: 'agent_response',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'agent-response-buffered-1',
        agent_id: 'agent-1',
        client_request_id: 'req-buffer-agent-response',
        content: 'Early final response',
      },
    },
    clientRequestId: 'req-buffer-agent-response',
    assertBefore: () => {
      expect(useMessageStore.getState().entities['agent-response-buffered-1']).toBeUndefined()
    },
    assertAfter: () => {
      expect(useMessageStore.getState().entities['agent-response-buffered-1']?.content).toBe('Early final response')
    },
  },
])('buffers early $label until the user message id is resolved', async ({ frame, clientRequestId, assertBefore, assertAfter }) => {
  await mountHook()

  await act(async () => {
    await capturedOnMessage!(frame as RoomSSEMessage)
  })

  assertBefore()
  expect(getResolvedMessageId(clientRequestId)).toBeUndefined()

  // Use the existing message-store fixture/helper to seed the server user message
  // before flushing; processing_status updates an existing user turn and must not
  // create that user entity from a child SSE frame.
  seedServerUserMessage({ id: 'user-msg-1', clientRequestId })
  resolveClientRequestMessageId(clientRequestId, 'user-msg-1')
  await flushPendingSseEvents(clientRequestId, capturedOnMessage!, 'user-msg-1')

  expect(getResolvedMessageId(clientRequestId)).toBe('user-msg-1')
  assertAfter()
})
```

- [ ] **Step 5: Run dispatch tests**

Run:

```bash
npx vitest run tests/unit/hooks/useRoomSSE.test.ts tests/unit/hooks/useRoomWebhook.test.ts
```

Expected: PASS after legacy `user_message` expectations are removed and final frame tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/hooks/useRoomSSE.ts src/hooks/room/useRoomSSEConnection.ts src/hooks/room/sse-handlers/dispatch.ts src/hooks/room/sse-handlers/correlation.ts src/hooks/room/sse-handlers/pending-turn-buffer.ts tests/unit/hooks/useRoomSSE.test.ts tests/unit/hooks/useRoomWebhook.test.ts
git commit -m "refactor: dispatch room sse by final frame type"
```

## Task 4: Update Final Event Handlers

**Hard ordering requirement:** after Task 4 Step 0, stop Task 4 and execute Task 5 Step 1 through Step 5. Return to Task 4 Step 1 only after the send API tests in Task 5 prove the object-parameter `sendUserMessage({ ..., dispatch })` boundary is in place. Do not run Task 4 handler checkpoints before this interlude.

**Files:**
- Modify: `src/lib/types/agent-group.ts`
- Modify: `src/hooks/room/sse-handlers/handlers/misc.ts`
- Modify: `src/hooks/room/sse-handlers/handlers/processing-status.ts`
- Modify: `src/hooks/room/processing-status-log.ts`
- Modify: `src/hooks/room/sse-handlers/handlers/agent-response.ts`
- Modify: `src/hooks/room/sse-handlers/handlers/task-submitted.ts`
- Modify: `src/hooks/room/sse-handlers/handlers/task-update.ts`
- Modify: `src/hooks/room/sse-handlers/handlers/artifact-update.ts`
- Modify: `src/hooks/room/sse-handlers/handlers/hitl.ts`
- Modify: `src/stores/streaming-store/index.ts`
- Modify: `src/hooks/useStreamBuffer.ts`
- Modify: `tests/unit/hooks/useRoomWebhook.test.ts`
- Modify: `tests/unit/hooks/double-send-guard.test.ts`
- Modify: `tests/unit/hooks/hitl-sse-handlers.test.ts`
- Modify: `tests/unit/hooks/room-lifecycle.test.ts`
- Modify: `tests/unit/stores/streaming-store.test.ts`
- Modify: `tests/unit/lib/streaming/display.test.ts`
- Modify: `tests/fixtures/index.ts`
- Modify: `tests/unit/stores/fixture-type-safety.test.ts`

- [ ] **Step 0: Add shared final dispatch and send input types before handler tests**

In `src/lib/types/agent-group.ts`, add the final XOR dispatch shape before any task updates tests or API code that import `MessageDispatchInput`:

```ts
export type MentionDispatchInput = {
  mentioned_agent_ids: [string, ...string[]]
  message_target_mode?: never
  target_group_id?: never
}

export type TargetModeDispatchInput =
  | {
      message_target_mode: 'room_default'
      target_group_id?: never
      mentioned_agent_ids?: never
    }
  | {
      message_target_mode: 'all_agents'
      target_group_id?: never
      mentioned_agent_ids?: never
    }
  | {
      message_target_mode: 'saved_group'
      target_group_id: string
      mentioned_agent_ids?: never
    }

export type MessageDispatchInput = MentionDispatchInput | TargetModeDispatchInput

export function isMentionDispatchInput(dispatch: MessageDispatchInput): dispatch is MentionDispatchInput {
  return Array.isArray(dispatch.mentioned_agent_ids) && dispatch.mentioned_agent_ids.length > 0
}

export function isMessageDispatchInput(value: unknown): value is MessageDispatchInput {
  if (!value || typeof value !== 'object') return false
  const dispatch = value as {
    mentioned_agent_ids?: unknown
    message_target_mode?: unknown
    target_group_id?: unknown
  }
  const keys = Object.keys(dispatch)
  const hasMentions = 'mentioned_agent_ids' in dispatch
  const hasMode = 'message_target_mode' in dispatch

  if (hasMentions && hasMode) return false
  if (hasMentions) {
    if (!keys.every((key) => key === 'mentioned_agent_ids')) return false
    return Array.isArray(dispatch.mentioned_agent_ids)
      && dispatch.mentioned_agent_ids.length > 0
      && dispatch.mentioned_agent_ids.every((id) => typeof id === 'string' && id.length > 0)
      && !('target_group_id' in dispatch)
  }
  if (dispatch.message_target_mode === 'room_default' || dispatch.message_target_mode === 'all_agents') {
    if (!keys.every((key) => key === 'message_target_mode')) return false
    return !('mentioned_agent_ids' in dispatch) && !('target_group_id' in dispatch)
  }
  if (dispatch.message_target_mode === 'saved_group') {
    if (!keys.every((key) => key === 'message_target_mode' || key === 'target_group_id')) return false
    return typeof dispatch.target_group_id === 'string'
      && dispatch.target_group_id.length > 0
      && !('mentioned_agent_ids' in dispatch)
  }
  return false
}

export function assertMessageDispatchInput(value: unknown): asserts value is MessageDispatchInput {
  if (!isMessageDispatchInput(value)) {
    throw new Error('Invalid MessageDispatchInput')
  }
}

export function resolveSelectedGroupDispatch(selectedGroup: string): TargetModeDispatchInput {
  switch (selectedGroup) {
    case BUILTIN_GROUP_ROOM_TEAM:
      return { message_target_mode: 'room_default' }
    case BUILTIN_GROUP_ALL_AGENTS:
      return { message_target_mode: 'all_agents' }
    default:
      return { message_target_mode: 'saved_group', target_group_id: selectedGroup }
  }
}
```

In `src/hooks/room/useSendMessage.ts`, export the final object input type before Task 4 tests import it:

```ts
export type SendUserMessageInput = {
  userInput: string
  quoteData?: QuoteData
  pendingAttachments?: PendingAttachment[]
  dispatch: MessageDispatchInput
}
```

- [ ] **Step 0.5: Execute the send-boundary interlude**

Run Task 5 Step 1 through Step 5 now. This is a hard prerequisite for the tests below because they call the final `sendUserMessage(input)` object API.

- [ ] **Step 1: Write failing handler tests for object details, queued, and partial responses**

In `tests/unit/hooks/useRoomWebhook.test.ts`, first add a local helper and update every `result.current.sendUserMessage('...')` call in this file to use the final object-parameter send boundary:

```ts
import type { SendUserMessageInput } from '@/hooks/room/useSendMessage'

async function sendWithRoomDefault(
  sendUserMessage: (input: SendUserMessageInput) => Promise<boolean>,
  text: string,
) {
  return sendUserMessage({
    userInput: text,
    dispatch: { message_target_mode: 'room_default' },
  })
}
```

For example:

```ts
await sendWithRoomDefault(result.current.sendUserMessage, 'Analyze current project status')
```

Apply the same pattern in `tests/unit/hooks/double-send-guard.test.ts` and `tests/unit/hooks/room-lifecycle.test.ts` so no test calls `sendUserMessage` without final dispatch.

Then update processing-status detail payloads from strings to objects and add:

```ts
it('records processing_status object details without accepting string details', async () => {
  const { result } = await mountHook()
  await act(async () => {
    await sendWithRoomDefault(result.current.sendUserMessage, 'Analyze current project status')
  })
  const userBefore = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.content === 'Analyze current project status')
  const clientRequestId = userBefore?.clientRequestId
  expect(clientRequestId).toBeDefined()

  await act(async () => {
    await capturedOnMessage!({
      type: 'processing_status',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'msg-1',
        client_request_id: clientRequestId,
        status: 'processing',
        details: { message: 'Dispatching agents' },
      },
    })
  })

  const user = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.clientRequestId === clientRequestId)
  expect(user?.processingStatusLogs?.map((entry) => entry.message)).toContain('Dispatching agents')
})

it('accepts processing_status details null without invalid-data logging', async () => {
  const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
  const { result } = await mountHook()
  await act(async () => {
    await sendWithRoomDefault(result.current.sendUserMessage, 'Null details status')
  })
  const userBefore = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.content === 'Null details status')
  const clientRequestId = userBefore?.clientRequestId
  expect(clientRequestId).toBeDefined()

  await act(async () => {
    await capturedOnMessage!({
      type: 'processing_status',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: userBefore?.id ?? null,
        client_request_id: clientRequestId,
        status: 'processing',
        details: null,
      },
    })
  })

  expect(debugSpy).not.toHaveBeenCalledWith(
    'Ignoring invalid processing_status data:',
    expect.anything(),
  )
  debugSpy.mockRestore()
})

it('debug-logs and ignores legacy string processing_status details', async () => {
  const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
  const { result } = await mountHook()
  await act(async () => {
    await sendWithRoomDefault(result.current.sendUserMessage, 'Reject legacy details')
  })
  const userBefore = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.content === 'Reject legacy details')
  const clientRequestId = userBefore?.clientRequestId
  expect(clientRequestId).toBeDefined()

  await act(async () => {
    await capturedOnMessage!({
      type: 'processing_status',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: userBefore?.id ?? null,
        client_request_id: clientRequestId,
        status: 'processing',
        details: 'legacy string',
      },
    })
  })

  const user = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.clientRequestId === clientRequestId)
  expect(user?.processingStatusLogs?.map((entry) => entry.message)).not.toContain('legacy string')
  expect(debugSpy).toHaveBeenCalledWith(
    'Ignoring invalid processing_status data:',
    'legacy string',
  )
  debugSpy.mockRestore()
})

it('debug-logs and ignores processing_status frames missing details', async () => {
  const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
  const { result } = await mountHook()
  await act(async () => {
    await sendWithRoomDefault(result.current.sendUserMessage, 'Reject missing details')
  })
  const userBefore = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.content === 'Reject missing details')
  const clientRequestId = userBefore?.clientRequestId
  expect(clientRequestId).toBeDefined()

  await act(async () => {
    await capturedOnMessage!({
      type: 'processing_status',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: userBefore?.id ?? null,
        client_request_id: clientRequestId,
        status: 'processing',
      },
    })
  })

  const user = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.clientRequestId === clientRequestId)
  expect(user?.processingStatusLogs?.map((entry) => entry.message)).not.toContain('undefined')
  expect(debugSpy).toHaveBeenCalledWith(
    'Ignoring processing_status without required object/null details:',
    expect.objectContaining({
      message_id: userBefore?.id ?? null,
      client_request_id: clientRequestId,
      status: 'processing',
    }),
  )
  debugSpy.mockRestore()
})

it('treats queued as a non-terminal processing status', async () => {
  const { result } = await mountHook()
  await act(async () => {
    await sendWithRoomDefault(result.current.sendUserMessage, 'Queue this')
  })
  const userBefore = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.content === 'Queue this')
  const clientRequestId = userBefore?.clientRequestId
  expect(clientRequestId).toBeDefined()

  await act(async () => {
    await capturedOnMessage!({
      type: 'processing_status',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'msg-1',
        client_request_id: clientRequestId,
        status: 'queued',
        details: { message: 'Queued for agents' },
      },
    })
  })

  expect(flags().processing).toBe(true)
})
```

Add an `agent_response_partial` test:

```ts
it('buffers agent_response_partial content until final agent_response arrives', async () => {
  const { result } = await mountHook()
  await act(async () => {
    await sendWithRoomDefault(result.current.sendUserMessage, 'Stream partial response')
  })
  const userBefore = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.content === 'Stream partial response')
  const clientRequestId = userBefore?.clientRequestId
  expect(clientRequestId).toBeDefined()
  resolveClientRequestMessageId(clientRequestId!, userBefore!.id)

  await act(async () => {
    await capturedOnMessage!({
      type: 'agent_response_partial',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'agent-msg-1',
        agent_id: 'agent-1',
        content_delta: 'partial text',
        client_request_id: clientRequestId,
      },
    })
  })

  const { useStreamingStore } = await import('@/stores/streaming-store')
  const partialBuffer = useStreamingStore.getState().buffers['agent-msg-1']
  expect(partialBuffer?.text).toBe('partial text')
  expect(partialBuffer?.clientRequestId).toBe(clientRequestId)
  expect(partialBuffer?.userMessageId).toBe(userBefore!.id)
  expect(useMessageStore.getState().entities['agent-msg-1']).toBeUndefined()
})
```

Add a buffered partial regression:

```ts
it('buffers agent_response_partial by client_request_id until the user message id is resolved', async () => {
  await mountHook()

  await act(async () => {
    await capturedOnMessage!({
      type: 'agent_response_partial',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'agent-msg-buffered',
        agent_id: 'agent-1',
        content_delta: 'early partial',
        client_request_id: 'req-buffer-partial',
      },
    })
  })

  const { useStreamingStore } = await import('@/stores/streaming-store')
  expect(useStreamingStore.getState().buffers['agent-msg-buffered']).toBeUndefined()

  resolveClientRequestMessageId('req-buffer-partial', 'user-msg-1')
  await flushPendingSseEvents('req-buffer-partial', capturedOnMessage!, 'user-msg-1')

  expect(useStreamingStore.getState().buffers['agent-msg-buffered']?.text).toBe('early partial')
})
```

Run: `npx vitest run tests/unit/hooks/useRoomWebhook.test.ts`

Expected: FAIL because details are currently string-only, `queued` is not handled, and partial responses are not dispatched.

- [ ] **Step 2: Convert processing details object to a display log message**

In `src/hooks/room/processing-status-log.ts`, add:

```ts
export function processingDetailsToLogMessage(
  details: Record<string, unknown> | null | undefined,
): string | undefined {
  if (!details) return undefined
  const message = details.message ?? details.status_message ?? details.stage ?? details.description
  if (typeof message === 'string' && message.trim()) return message.trim()
  const json = JSON.stringify(details)
  return json === '{}' ? undefined : json
}
```

Do not accept `string` as an input type.

In `src/hooks/room/sse-handlers/handlers/processing-status.ts`, add a runtime guard before any processing-status logic uses `details`:

```ts
const PROCESSING_STATUS_VALUES = new Set<string>(Object.values(PROCESSING_STATUS))

function hasValidProcessingDetails(
  details: unknown,
): details is Record<string, unknown> | null {
  return details === null || (typeof details === 'object' && !Array.isArray(details))
}

function isProcessingStatusData(data: unknown): data is ProcessingStatusData {
  if (!data || typeof data !== 'object') return false
  const value = data as Record<string, unknown>
  if (!Object.prototype.hasOwnProperty.call(value, 'message_id')) return false
  if (value.message_id !== null && typeof value.message_id !== 'string') return false
  if (typeof value.client_request_id !== 'string' || value.client_request_id.length === 0) return false
  if (typeof value.status !== 'string' || !PROCESSING_STATUS_VALUES.has(value.status)) return false
  if (!Object.prototype.hasOwnProperty.call(value, 'details')) return false
  return hasValidProcessingDetails(value.details)
}
```

At the top of `handleProcessingStatus`, before reading any event-specific field, add:

```ts
if (!isProcessingStatusData(sseMessage.data)) {
  const details = sseMessage.data && typeof sseMessage.data === 'object'
    ? (sseMessage.data as Record<string, unknown>).details
    : undefined
  const message = details === undefined
    ? 'Ignoring processing_status without required object/null details:'
    : 'Ignoring invalid processing_status data:'
  console.debug(message, details ?? sseMessage.data)
  return
}
```

Then import `processingDetailsToLogMessage` and replace all `sseMessage.data.details` calls that expect strings with:

```ts
const detailMessage = processingDetailsToLogMessage(sseMessage.data.details)
```

Use `detailMessage` for `appendProcessingLog` and failed banners:

```ts
banner.error(`Processing failed: ${detailMessage || 'Unknown error'}`)
```

- [ ] **Step 3: Treat `queued` and `awaiting_input` like active processing statuses**

In `handleProcessingStatus`, replace:

```ts
if (status === PROCESSING_STATUS.PROCESSING) {
```

with:

```ts
if (
  status === PROCESSING_STATUS.QUEUED
  || status === PROCESSING_STATUS.PROCESSING
  || status === PROCESSING_STATUS.AWAITING_INPUT
) {
```

Keep terminal handling driven by `isProcessingDone(status)`.
`awaiting_input` is non-terminal: it keeps the originating turn active and appends the status log, while HITL UI state is driven by the separate `hitl_input_requested` frame.

Add a regression:

```ts
it('treats awaiting_input as non-terminal and keeps the turn active', async () => {
  const { result } = await mountHook()
  await act(async () => {
    await sendWithRoomDefault(result.current.sendUserMessage, 'Need human input')
  })
  const userBefore = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.content === 'Need human input')
  const clientRequestId = userBefore?.clientRequestId
  expect(clientRequestId).toBeDefined()

  await act(async () => {
    await capturedOnMessage!({
      type: 'processing_status',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: userBefore?.id ?? null,
        client_request_id: clientRequestId,
        status: 'awaiting_input',
        details: { message: 'Waiting for user approval' },
      },
    })
  })

  const user = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.clientRequestId === clientRequestId)
  expect(isProcessingDone('awaiting_input')).toBe(false)
  expect(user?.processingStatus).toBe('awaiting_input')
  expect(user?.processingStatusLogs?.map((entry) => entry.message)).toContain('Waiting for user approval')
})
```

- [ ] **Step 4: Add final misc handlers**

In `src/hooks/room/sse-handlers/handlers/misc.ts`, update `handleError` first. Global errors may omit `client_request_id` only when they also omit turn identifiers; any error with `message_id` or `agent_id` is turn-scoped and must be anchored by `client_request_id`:

```ts
function isTurnScopedError(data: ErrorData): boolean {
  return 'client_request_id' in data || 'message_id' in data || 'agent_id' in data
}

export function handleError(_ctx: SSEHandlerDeps, sseMessage: RoomSSEFrameMap['error']): void {
  const errorData = sseMessage.data
  if (isTurnScopedError(errorData) && !errorData.client_request_id) {
    console.debug('Ignoring turn-scoped error without client_request_id:', errorData)
    return
  }

  // keep existing banner behavior after the anchoring guard
}
```

Add a regression to `tests/unit/hooks/useRoomWebhook.test.ts`:

```ts
it('drops turn-scoped error frames without client_request_id', async () => {
  const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
  await mountHook()

  await act(async () => {
    await capturedOnMessage!({
      type: 'error',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        error: 'Task failed',
        message_id: 'msg-error-without-client-request',
      },
    })
  })

  expect(debugSpy).toHaveBeenCalledWith(
    'Ignoring turn-scoped error without client_request_id:',
    expect.objectContaining({ message_id: 'msg-error-without-client-request' }),
  )
  debugSpy.mockRestore()
})
```

Add a rate-limit payload regression:

```ts
it('preserves rate limit quota fields on error frames', async () => {
  const bannerError = vi.spyOn(banner, 'error')
  await mountHook()

  await act(async () => {
    await capturedOnMessage!({
      type: 'error',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        error: 'Rate limit exceeded',
        error_type: 'rate_limit_exceeded',
        retry_after_seconds: 120,
        user_requests_used: 10,
        user_requests_limit: 10,
        system_requests_used: 100,
        system_requests_limit: 100,
      },
    })
  })

  const expectedDescription = 'Retry after 2 minutes. User requests: 10/10. System requests: 100/100.'
  expect(bannerError).toHaveBeenCalledWith(
    expect.stringContaining('Rate limit exceeded'),
    expect.objectContaining({
      description: expectedDescription,
    }),
  )
})
```

Then replace the Task 3 local no-op misc stubs with imported final misc handlers. Remove the local stub declarations from `dispatch.ts` when importing these final handlers, so there are no duplicate function names:

```ts
import type { RoomSSEFrameMap } from '@/lib/types/sse'

export function handleConnected(sseMessage: RoomSSEFrameMap['connected']): void {
  console.debug('Room SSE connected:', sseMessage.data.connection_id)
}

export function handleCancellation(
  ctx: SSEHandlerDeps,
  sseMessage: RoomSSEFrameMap['cancellation'],
): void {
  console.debug('Room SSE cancellation event:', sseMessage.data)
  ctx.setCancelling(false)
  ctx.lifecycle.disarmCancelTimeout()
}

export function handleHubAgentEvent(sseMessage: RoomSSEFrameMap['hub_agent_event']): void {
  console.debug('Room SSE hub_agent_event:', sseMessage.data)
}

export function handleDebateRound(sseMessage: RoomSSEFrameMap['debate_round']): void {
  console.debug('Room SSE debate_round:', sseMessage.data)
}
```

Remove `handleTurnEvent`.

- [ ] **Step 5: Add `agent_response_partial` streaming**

In `src/hooks/room/sse-handlers/handlers/agent-response.ts`, add:

```ts
import type { RoomSSEFrameMap } from '@/lib/types/sse'
import type { CorrelationResult } from '@/hooks/room/sse-handlers/correlation'
import type { SSEHandlerDeps } from '@/hooks/room/sse-handlers/types'
import type { ArtifactData } from '@/stores/message-store/types'
import { useStreamingStore } from '@/stores/streaming-store'
import { getResolvedMessageId } from '@/hooks/room/sse-handlers/pending-turn-buffer'

function textPartialToArtifact(messageId: string, content: string): ArtifactData {
  return {
    artifactId: `${messageId}-agent-response-partial`,
    name: 'Response',
    parts: [{ kind: 'text', text: content }],
    isStreaming: true,
  }
}

export function handleAgentResponsePartial(
  ctx: SSEHandlerDeps,
  sseMessage: RoomSSEFrameMap['agent_response_partial'],
  correlation: CorrelationResult,
): void {
  const { message_id, content_delta } = sseMessage.data
  if (!message_id || typeof content_delta !== 'string') return
  useStreamingStore.getState().append(
    message_id,
    ctx.roomId,
    textPartialToArtifact(message_id, content_delta),
    true,
    {
      clientRequestId: correlation.clientReqId,
      userMessageId: correlation.clientReqId
        ? getResolvedMessageId(correlation.clientReqId)
        : undefined,
    },
  )
}
```

Remove the Task 3 local `handleAgentResponsePartial` stub from `dispatch.ts` and import this final handler instead.

Extend `useStreamingStore.append` and `StreamBuffer` with optional metadata:

```ts
type StreamBufferMetadata = {
  clientRequestId?: string
  userMessageId?: string
}

interface StreamBuffer {
  text: string
  artifacts: ArtifactData[]
  isComplete: boolean
  roomId: string
  lastUpdatedAt: number
  clientRequestId?: string
  userMessageId?: string
}

append: (
  id: string,
  roomId: string,
  chunk: ArtifactData,
  isAppend: boolean,
  metadata?: StreamBufferMetadata,
) => void
```

When appending later chunks, preserve existing metadata unless the new chunk supplies a more specific value:

```ts
clientRequestId: metadata?.clientRequestId ?? existing?.clientRequestId,
userMessageId: metadata?.userMessageId ?? existing?.userMessageId,
```

Add a regression to `tests/unit/stores/streaming-store.test.ts`:

```ts
it('preserves stream buffer client request metadata across appended chunks', () => {
  useStreamingStore.getState().append('msg-1', 'room-1', makeChunk('first'), false, {
    clientRequestId: 'req-1',
    userMessageId: 'user-msg-1',
  })
  useStreamingStore.getState().append('msg-1', 'room-1', makeChunk(' second'), true)

  const buffer = useStreamingStore.getState().buffers['msg-1']
  expect(buffer.clientRequestId).toBe('req-1')
  expect(buffer.userMessageId).toBe('user-msg-1')
  expect(buffer.text).toBe('first second')
})
```

Do not write partial content into `useMessageStore`; final `agent_response` or `task_update` remains the durable write. The streaming buffer must still preserve `clientRequestId` and resolved user message id so the UI can prove the partial belongs to the originating optimistic user turn.

When final `agent_response` arrives for the same `message_id`, write the durable message through the existing final response path and clear the streaming buffer for that `message_id` with `useStreamingStore.getState().clear(message_id)`. The final durable entity must not duplicate the partial as a separate message.

Add a final-flush regression:

```ts
it('clears partial streaming buffer when final agent_response arrives', async () => {
  const { result } = await mountHook()
  await act(async () => {
    await sendWithRoomDefault(result.current.sendUserMessage, 'Stream then finalize')
  })
  const userBefore = Object.values(useMessageStore.getState().entities)
    .find((entity) => entity.messageType === 'user' && entity.content === 'Stream then finalize')
  const clientRequestId = userBefore?.clientRequestId
  expect(clientRequestId).toBeDefined()
  resolveClientRequestMessageId(clientRequestId!, userBefore!.id)

  await act(async () => {
    await capturedOnMessage!({
      type: 'agent_response_partial',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'agent-msg-finalize',
        agent_id: 'agent-1',
        content_delta: 'partial ',
        client_request_id: clientRequestId,
      },
    })
  })
  expect(useStreamingStore.getState().buffers['agent-msg-finalize']).toBeDefined()

  await act(async () => {
    await capturedOnMessage!({
      type: 'agent_response',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'agent-msg-finalize',
        agent_id: 'agent-1',
        content: 'partial final',
        client_request_id: clientRequestId!,
      },
    })
  })

  expect(useMessageStore.getState().entities['agent-msg-finalize']?.content).toBe('partial final')
  expect(useStreamingStore.getState().buffers['agent-msg-finalize']).toBeUndefined()
})
```


- [ ] **Step 6: Update handler parameter types**

Change event-specific handlers to accept the corresponding `RoomSSEFrameMap[...]` type:

```ts
handleTaskSubmitted(ctx, sseMessage: RoomSSEFrameMap['task_submitted'], correlation)
handleTaskUpdate(ctx, sseMessage: RoomSSEFrameMap['task_update'], correlation)
handleArtifactUpdate(ctx, sseMessage: RoomSSEFrameMap['artifact_update'], correlation)
handleHitlInputRequested(ctx, sseMessage: RoomSSEFrameMap['hitl_input_requested'], correlation)
handleHitlStatusUpdate(ctx, sseMessage: RoomSSEFrameMap['hitl_status_update'], correlation)
handleRunEvent(ctx, lifecycle, sseMessage: RoomSSEFrameMap['run_event'])
handleError(ctx, sseMessage: RoomSSEFrameMap['error'])
```

For nullable final fields, normalize before store writes:

```ts
agentId: sseMessage.data.agent_id ?? undefined
relatedMessageId: sseMessage.data.related_message_id ?? undefined
stepNumber: sseMessage.data.step_number ?? undefined
totalSteps: sseMessage.data.total_steps ?? undefined
```

- [ ] **Step 7: Preserve artifact update turn metadata**

In `src/hooks/room/sse-handlers/handlers/artifact-update.ts`, pass correlation metadata through `useStreamingStore`, which is the app's live artifact update surface. Do not verify artifact anchoring only by checking `useMessageStore` existence:

```ts
handleArtifactUpdate(ctx, sseMessage: RoomSSEFrameMap['artifact_update'], correlation)
```

When writing/appending the artifact, preserve:

```ts
clientRequestId: correlation.clientReqId,
userMessageId: correlation.clientReqId
  ? getResolvedMessageId(correlation.clientReqId)
  : undefined,
```

Extend `StreamBuffer` metadata in `src/stores/streaming-store/index.ts` the same way as `agent_response_partial`, and keep `src/hooks/useStreamBuffer.ts` consumers compatible with the extended type. Add a regression that verifies the real artifact storage surface:

```ts
expect(getResolvedMessageId('req-buffer-artifact')).toBe('user-msg-1')
expect(useStreamingStore.getState().buffers['artifact-buffered-1']?.clientRequestId).toBe('req-buffer-artifact')
expect(useStreamingStore.getState().buffers['artifact-buffered-1']?.userMessageId).toBe('user-msg-1')
```

- [ ] **Step 8: Run handler tests**

Run:

```bash
npx vitest run tests/unit/hooks/useRoomWebhook.test.ts tests/unit/hooks/hitl-sse-handlers.test.ts tests/unit/hooks/room-lifecycle.test.ts
```

Expected: PASS after Task 5's final dispatch send boundary is in place, test fixtures use `data: {}` for heartbeat, and `processing_status.details` uses object/null.

Also update `tests/fixtures/index.ts` shared SSE helpers so they emit final envelopes with required `data`, and require or accept `client_request_id` for turn-correlated event builders. Add a fixture regression:

```ts
expect(createSSEMessage('heartbeat', {})).toMatchObject({
  type: 'heartbeat',
  room_id: expect.any(String),
  timestamp: expect.any(String),
  data: {},
})

expect(createProcessingStatusSSE({
  status: 'processing',
  client_request_id: 'req-1',
  details: { message: 'Working' },
}).data.client_request_id).toBe('req-1')
```

- [ ] **Step 9: Commit**

```bash
git add src/lib/types/agent-group.ts src/hooks/room/sse-handlers/handlers src/hooks/room/processing-status-log.ts src/stores/streaming-store/index.ts src/hooks/useStreamBuffer.ts tests/unit/hooks/useRoomWebhook.test.ts tests/unit/hooks/double-send-guard.test.ts tests/unit/hooks/hitl-sse-handlers.test.ts tests/unit/hooks/room-lifecycle.test.ts tests/unit/stores/streaming-store.test.ts tests/unit/lib/streaming/display.test.ts tests/fixtures/index.ts tests/unit/stores/fixture-type-safety.test.ts
git commit -m "refactor: handle final room sse events"
```

## Task 5: Replace Send Message Routing Payload

**Files:**
- Modify: `src/lib/api/room.ts`
- Modify: `src/lib/types/request.ts`
- Modify: `src/hooks/room/useSendMessage.ts`
- Modify: `src/lib/types/agent-group.ts`
- Modify: `tests/unit/lib/room-api.test.ts`
- Modify: `tests/unit/hooks/double-send-guard.test.ts`

- [ ] **Step 1: Write failing API payload tests**

If following the Task 4 ordering note, write these tests immediately after Task 4 Step 0 before implementing Task 5 Step 2 through Step 4. Do not run Task 4 handler PASS checkpoints until these tests have gone red and the send API conversion below has gone green.

In `tests/unit/lib/room-api.test.ts`, replace target-group assertions with final payload assertions:

```ts
import type { MessageDispatchInput } from '@/lib/types/agent-group'

// Cover all routing branches: room_default, all_agents, saved_group, and mentions.
// Every branch must assert `client_request_id` and absence of the legacy routing field.

it('sends room_default routing with client_request_id and no legacy routing field', async () => {
  let capturedBody: Record<string, unknown> | null = null
  server.use(
    http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
      capturedBody = await request.json() as Record<string, unknown>
      return HttpResponse.json({ success: true, message_id: 'msg-1' })
    })
  )

  await SendMessage({
    roomId: 'room-1',
    userInput: 'Hello',
    userId: 'user-1',
    userName: 'Test User',
    clientRequestId: 'cr-uuid-123',
    dispatch: { message_target_mode: 'room_default' },
  })

  expect(capturedBody).toHaveProperty('client_request_id', 'cr-uuid-123')
  expect(capturedBody).toHaveProperty('message_target_mode', 'room_default')
  expect(capturedBody).not.toHaveProperty('target_group')
})

it('sends all_agents routing with client_request_id and no legacy routing field', async () => {
  let capturedBody: Record<string, unknown> | null = null
  server.use(
    http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
      capturedBody = await request.json() as Record<string, unknown>
      return HttpResponse.json({ success: true, message_id: 'msg-1' })
    })
  )

  await SendMessage({
    roomId: 'room-1',
    userInput: 'Hello all agents',
    userId: 'user-1',
    userName: 'Test User',
    clientRequestId: 'cr-all-agents-123',
    dispatch: { message_target_mode: 'all_agents' },
  })

  expect(capturedBody).toHaveProperty('client_request_id', 'cr-all-agents-123')
  expect(capturedBody).toHaveProperty('message_target_mode', 'all_agents')
  expect(capturedBody).not.toHaveProperty('mentioned_agent_ids')
  expect(capturedBody).not.toHaveProperty('target_group_id')
  expect(capturedBody).not.toHaveProperty('target_group')
})

it('sends mentioned_agent_ids without message_target_mode or target_group_id', async () => {
  let capturedBody: Record<string, unknown> | null = null
  server.use(
    http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
      capturedBody = await request.json() as Record<string, unknown>
      return HttpResponse.json({ success: true, message_id: 'msg-1' })
    })
  )

  await SendMessage({
    roomId: 'room-1',
    userInput: 'Hello @Alpha',
    userId: 'user-1',
    userName: 'Test User',
    clientRequestId: 'cr-mention-123',
    dispatch: { mentioned_agent_ids: ['agent-a', 'agent-b'] },
  })

  expect(capturedBody).toHaveProperty('mentioned_agent_ids', ['agent-a', 'agent-b'])
  expect(capturedBody).toHaveProperty('client_request_id', 'cr-mention-123')
  expect(capturedBody).not.toHaveProperty('message_target_mode')
  expect(capturedBody).not.toHaveProperty('target_group_id')
  expect(capturedBody).not.toHaveProperty('target_group')
})

it('sends saved_group routing without mentions or legacy routing field', async () => {
  let capturedBody: Record<string, unknown> | null = null
  server.use(
    http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
      capturedBody = await request.json() as Record<string, unknown>
      return HttpResponse.json({ success: true, message_id: 'msg-1' })
    })
  )

  await SendMessage({
    roomId: 'room-1',
    userInput: 'Hello saved group',
    userId: 'user-1',
    userName: 'Test User',
    clientRequestId: 'cr-saved-group-123',
    dispatch: { message_target_mode: 'saved_group', target_group_id: 'grp-123' },
  })

  expect(capturedBody).toHaveProperty('client_request_id', 'cr-saved-group-123')
  expect(capturedBody).toHaveProperty('message_target_mode', 'saved_group')
  expect(capturedBody).toHaveProperty('target_group_id', 'grp-123')
  expect(capturedBody).not.toHaveProperty('mentioned_agent_ids')
  expect(capturedBody).not.toHaveProperty('target_group')
})

it('rejects malformed dispatch with mentions and message_target_mode', async () => {
  await expect(SendMessage({
    roomId: 'room-1',
    userInput: 'Invalid mixed dispatch',
    userId: 'user-1',
    userName: 'Test User',
    clientRequestId: 'cr-invalid-mixed',
    dispatch: {
      mentioned_agent_ids: ['agent-a'],
      message_target_mode: 'all_agents',
    } as unknown as MessageDispatchInput,
  })).rejects.toThrow('Invalid MessageDispatchInput')
})

it('rejects empty mentioned_agent_ids', async () => {
  await expect(SendMessage({
    roomId: 'room-1',
    userInput: 'Invalid empty mentions',
    userId: 'user-1',
    userName: 'Test User',
    clientRequestId: 'cr-invalid-empty-mentions',
    dispatch: { mentioned_agent_ids: [] } as unknown as MessageDispatchInput,
  })).rejects.toThrow('Invalid MessageDispatchInput')
})

it('rejects saved_group without target_group_id', async () => {
  await expect(SendMessage({
    roomId: 'room-1',
    userInput: 'Invalid saved group',
    userId: 'user-1',
    userName: 'Test User',
    clientRequestId: 'cr-invalid-saved-group',
    dispatch: { message_target_mode: 'saved_group' } as unknown as MessageDispatchInput,
  })).rejects.toThrow('Invalid MessageDispatchInput')
})

it('rejects dispatch objects with extra legacy routing fields', async () => {
  const legacyRoutingKey = 'target' + '_group'
  await expect(SendMessage({
    roomId: 'room-1',
    userInput: 'Invalid legacy extra field',
    userId: 'user-1',
    userName: 'Test User',
    clientRequestId: 'cr-invalid-extra-field',
    dispatch: {
      message_target_mode: 'room_default',
      [legacyRoutingKey]: 'all_agents',
    } as unknown as MessageDispatchInput,
  })).rejects.toThrow('Invalid MessageDispatchInput')
})
```

Run: `npx vitest run tests/unit/lib/room-api.test.ts`

Expected: FAIL because `SendMessage` still takes positional args and sends the legacy routing field.

- [ ] **Step 2: Add final send-message request types**

In `src/lib/types/request.ts`, add:

```ts
type SendMessageBasePayload = {
  message: unknown
  client_request_id: string
}

export type SendMessagePayload = SendMessageBasePayload & (
  | {
      mentioned_agent_ids: [string, ...string[]]
      message_target_mode?: never
      target_group_id?: never
    }
  | {
      message_target_mode: 'room_default' | 'all_agents'
      mentioned_agent_ids?: never
      target_group_id?: never
    }
  | {
      message_target_mode: 'saved_group'
      target_group_id: string
      mentioned_agent_ids?: never
    }
)
```

If the endpoint still needs existing top-level `room_id`, `user_id`, `user_name`, `user_input`, or `attachments`, model them as an internal extension in `src/lib/api/room.ts`, not as legacy routing fields:

```ts
type SendMessageRequestBody = SendMessagePayload & {
  room_id: string
  user_id: string
  user_name: string
  user_input: string
  attachments?: Array<{ file_id: string }>
}
```

- [ ] **Step 3: Convert `SendMessage` to an object-parameter API**

In `src/lib/api/room.ts`, replace the positional `SendMessage` signature with:

```ts
import {
  assertMessageDispatchInput,
  isMentionDispatchInput,
  type MessageDispatchInput,
} from '@/lib/types/agent-group'

export interface SendMessageParams {
  roomId: string
  userInput: string
  getToken?: () => Promise<string | null>
  userId?: string
  userName?: string
  relatedMessageId?: string | null
  quotedText?: string | null
  quotedSenderName?: string | null
  attachments?: Array<{ file_id: string }>
  dispatch: MessageDispatchInput
  clientRequestId: string
  structuredQuote?: RoomQuoteWire | null
}

export async function SendMessage(params: SendMessageParams): Promise<RoomCenterUserMessageResponse> {
  const {
    roomId,
    userInput,
    getToken,
    userId,
    userName,
    relatedMessageId,
    quotedText,
    quotedSenderName,
    attachments,
    dispatch,
    clientRequestId,
    structuredQuote,
  } = params
  assertMessageDispatchInput(dispatch)
  // build message as today, using roomId/userInput/userId
}
```

Build `requestData` with no legacy routing field:

```ts
const baseRequestData = {
  room_id: roomId,
  user_id: userId || '',
  user_name: userName || '',
  user_input: userInput,
  message,
  client_request_id: clientRequestId,
}

let requestData: SendMessageRequestBody
if (isMentionDispatchInput(dispatch)) {
  requestData = {
    ...baseRequestData,
    mentioned_agent_ids: dispatch.mentioned_agent_ids,
  }
} else if (dispatch.message_target_mode === 'saved_group') {
  requestData = {
    ...baseRequestData,
    message_target_mode: 'saved_group',
    target_group_id: dispatch.target_group_id,
  }
} else {
  requestData = {
    ...baseRequestData,
    message_target_mode: dispatch.message_target_mode,
  }
}
```

Do not add the legacy routing field anywhere in the request body.

- [ ] **Step 4: Update `useSendMessage` call site**

In `src/hooks/room/useSendMessage.ts`, make `dispatch` required at the room send boundary. Callers must pass either explicit mention routing or `gm.resolvedTargetMode`; do not default missing dispatch to `all_agents`:

```ts
if (!dispatch) {
  console.debug('Blocked send without final MessageDispatchInput')
  return false
}

const createResponse = await SendMessage({
  roomId,
  userInput,
  getToken,
  userId,
  userName,
  relatedMessageId: structuredQuote ? null : (quoteData?.messageId ?? null),
  quotedText: structuredQuote ? null : (quoteData?.content ?? null),
  quotedSenderName: structuredQuote ? null : (quoteData?.senderName ?? null),
  attachments: uploadedAttachments,
  dispatch,
  clientRequestId,
  structuredQuote,
})
```

Keep `crypto.randomUUID()` generation in `useSendMessage`; every send path must pass that value to the API. The same generated value must be stored on the optimistic user message as `clientRequestId`, sent as `client_request_id`, mapped to the real server `message_id`, and reused by buffered SSE fixtures/events as the turn anchor.

After `createResponse.message_id` is known and the optimistic user id is swapped to the real server user message id, explicitly resolve and flush buffered SSE for that anchor:

```ts
resolveClientRequestMessageId(clientRequestId, messageId)
if (onPostMessageIdResolved) {
  await onPostMessageIdResolved(clientRequestId, messageId)
}
```

`onPostMessageIdResolved` must flush pending buffered events for `clientRequestId` only after `resolveClientRequestMessageId` has recorded the mapping.

Add a regression to `tests/unit/hooks/useRoomWebhook.test.ts`:

```ts
import type { SendMessageParams } from '@/lib/api/room'

it('resolves client_request_id to the server user message id and flushes buffered SSE after send succeeds', async () => {
  const { result } = await mountHook()

  // Expose the existing mocked SendMessage function as mockSendMessage in this test file
  // so the test captures the generated clientRequestId from the API call.
  const mockSendMessage = vi.mocked(SendMessage)
  let capturedClientRequestId: string | undefined
  mockSendMessage.mockImplementationOnce(async (params: SendMessageParams) => {
    capturedClientRequestId = params.clientRequestId
    await capturedOnMessage!({
      type: 'task_submitted',
      room_id: 'room-1',
      timestamp: new Date().toISOString(),
      data: {
        message_id: 'task-buffered-before-http',
        task_id: 'task-1',
        agent_name: 'Agent',
        agent_id: 'agent-1',
        status: 'working',
        related_message_id: 'msg-1',
        client_request_id: params.clientRequestId,
      },
    })
    return { success: true, message_id: 'msg-1' }
  })

  await act(async () => {
    await result.current.sendUserMessage({
      userInput: 'Flush buffered after send',
      dispatch: { message_target_mode: 'room_default' },
    })
  })

  expect(capturedClientRequestId).toBeDefined()
  const serverUser = useMessageStore.getState().entities['msg-1']
  expect(serverUser?.messageType).toBe('user')
  expect(serverUser?.clientRequestId).toBe(capturedClientRequestId)
  expect(getResolvedMessageId(capturedClientRequestId!)).toBe('msg-1')
  expect(useMessageStore.getState().entities['task-buffered-before-http']).toBeDefined()
})
```

- [ ] **Step 5: Remove send-path dependency on `targetGroup`**

Remove the `targetGroup` argument from `useSendMessage.sendUserMessage`. Use the `SendUserMessageInput` type exported in Task 4 Step 0; its object-based shape makes `dispatch` required without placing it after optional positional parameters:

```ts
sendUserMessage(input: SendUserMessageInput): Promise<boolean>
```

Do not pass `targetGroup` into `SendMessage` or use it to derive routing inside `useSendMessage`; routing must already be represented by `dispatch`.

- [ ] **Step 6: Run send API tests**

Run:

```bash
npx vitest run tests/unit/lib/room-api.test.ts tests/unit/hooks/double-send-guard.test.ts
```

Expected: PASS with all legacy routing assertions removed or inverted to `not.toHaveProperty('target_group')`.

- [ ] **Step 7: Commit**

```bash
git add src/lib/api/room.ts src/lib/types/request.ts src/hooks/room/useSendMessage.ts src/lib/types/agent-group.ts tests/unit/lib/room-api.test.ts tests/unit/hooks/double-send-guard.test.ts
git commit -m "refactor: send final room message routing payload"
```

## Task 6: Pass Mention IDs Through the UI Instead of Re-parsing in the Send API

**Files:**
- Modify: `src/components/room-chat-input.tsx`
- Modify: `src/components/composer/ComposerShell.tsx`
- Modify: `src/components/room-page-shell.tsx`
- Modify: `src/app/c/chat/page.tsx`
- Modify: `src/app/c/room/[id]/page.tsx`
- Modify: `src/hooks/useGroupManagement.ts`
- Modify: `src/hooks/useChatRoomCreation.ts`
- Modify: `src/stores/room-ui-store.ts`
- Modify: `tests/unit/components/room-chat-input-mention.test.tsx`
- Modify: `tests/unit/components/room-page-prefill.test.tsx`
- Modify: `tests/unit/hooks/useGroupManagement.test.ts`
- Modify: `tests/unit/hooks/useGroupManagement-empty-room.test.ts`
- Modify: `tests/unit/hooks/useChatRoomCreation.test.ts`
- Modify: `tests/unit/stores/room-ui-store.test.ts`
- Modify: `tests/unit/stores/scope-and-stale.test.ts`

- [ ] **Step 1: Write failing mention propagation test**

In `tests/unit/components/room-chat-input-mention.test.tsx`, add:

```ts
it('submits mentioned agent ids separately from the message text', async () => {
  const onSubmit = vi.fn()
  const { container } = renderInput({ onSubmit })
  const editor = getEditor(container)

  typeInEditor(editor, '@')
  fireEvent.click(screen.getByText('Alpha Agent'))

  await waitFor(() => {
    expect(editor.querySelector('.room-mention')).toBeTruthy()
  })

  fireEvent.keyDown(editor, { key: 'Enter' })

  expect(onSubmit).toHaveBeenCalled()
  expect(onSubmit.mock.calls[0][3]).toEqual({ mentioned_agent_ids: ['a-1'] })
  expect(onSubmit.mock.calls[0][1]).toBeUndefined()
})
```

Run: `npx vitest run tests/unit/components/room-chat-input-mention.test.tsx`

Expected: FAIL because `onSubmit` currently receives message, target group, quote, and attachments instead of a single final dispatch argument.

- [ ] **Step 2: Extend composer submit signatures**

Reuse the `MessageDispatchInput` and `isMentionDispatchInput` exports added in Task 4. Do not add a second duplicate definition. Component code should import the shared type:

```ts
import type { MessageDispatchInput } from '@/lib/types/agent-group'
```

In `src/components/room-chat-input.tsx`, replace the submit signature with a single dispatch argument:

```ts
onSubmit: (
  message: string,
  quote?: QuoteData | null,
  attachments?: PendingAttachment[],
  dispatch?: MessageDispatchInput,
) => void
```

At submit time:

```ts
const mentionedAgentIds = mentionedAgents.map((agent) => agent.id)
const dispatch: MessageDispatchInput =
  mentionedAgentIds.length > 0
    ? { mentioned_agent_ids: mentionedAgentIds as [string, ...string[]] }
    : resolveSelectedGroupDispatch(selectedGroup ?? BUILTIN_GROUP_ALL_AGENTS)
onSubmit(
  trimmedMessage,
  quote,
  submittedAttachments,
  dispatch,
)
```

Update `TimelineAdapter.onSendMessage` and `ComposerShellAdapter.onSendMessage` to use the same signature instead of `(...args: any[])`.

In `src/hooks/useGroupManagement.ts`, derive `resolvedTargetMode` with `resolveSelectedGroupDispatch(selectedGroup)` so selected saved groups always become final routing:

```ts
const resolvedTargetMode: TargetModeDispatchInput = useMemo(
  () => resolveSelectedGroupDispatch(selectedGroup),
  [selectedGroup],
)
```

Add or keep a saved-group conversion regression:

```ts
expect(resolveSelectedGroupDispatch('grp-my-saved')).toEqual({
  message_target_mode: 'saved_group',
  target_group_id: 'grp-my-saved',
})
expect(result.current.resolvedTargetMode).toEqual({
  message_target_mode: 'saved_group',
  target_group_id: 'grp-my-saved',
})
```

Replace all `normalizeLegacyTargetGroup` imports/usages with `resolveSelectedGroupDispatch`, then delete the old helper and its legacy-routing comment so Task 7's exact implementation scan stays clean. Update `tests/unit/stores/scope-and-stale.test.ts`, `tests/unit/hooks/useGroupManagement.test.ts`, and `tests/unit/hooks/useGroupManagement-empty-room.test.ts` to assert `resolveSelectedGroupDispatch` instead of the legacy helper name.

- [ ] **Step 3: Carry final dispatch through new-room handoff**

In `src/stores/room-ui-store.ts`, extend `PendingRoomData` to persist final routing, not only a legacy selected group id:

```ts
import type { MessageDispatchInput } from '@/lib/types/agent-group'

interface PendingRoomData {
  initialMessage: string
  targetGroup?: string
  dispatch?: MessageDispatchInput
  attachments?: PendingAttachment[]
  handoffMode?: 'autosend' | 'prefill'
}
```

Keep `targetGroup` only for existing UI display/handoff tests that still inspect the selected group; room autosend must require `dispatch` and must not derive routing from `targetGroup`.

In `src/hooks/useChatRoomCreation.ts`, add `dispatch?: MessageDispatchInput` to `CreateRoomOptions`. Persist final dispatch:

```ts
const handoffDispatch = options.dispatch

useRoomUiStore.getState().setPendingRoomData(roomId, {
  initialMessage: userMessage,
  targetGroup: handoffTargetGroup,
  dispatch: handoffDispatch,
  attachments: options.attachments,
})
```

In `src/app/c/chat/page.tsx`, update `handleSubmit` to accept the single dispatch argument from the composer and pass it through:

```ts
import { isMentionDispatchInput } from '@/lib/types/agent-group'

const finalDispatch = dispatch ?? gm.resolvedTargetMode

targetGroup: isMentionDispatchInput(finalDispatch) ? undefined : gm.selectedGroup,
dispatch: finalDispatch,
```

For selected saved groups without mentions, assert the create-room path passes final dispatch:

```ts
expect(mockCreateRoom).toHaveBeenCalledWith(expect.objectContaining({
  targetGroup: 'grp-saved-123',
  dispatch: {
    message_target_mode: 'saved_group',
    target_group_id: 'grp-saved-123',
  },
}))
```

- [ ] **Step 4: Use mention IDs in the room page send flow**

In `src/app/c/room/[id]/page.tsx`, update `handleSendMessage` signature:

```ts
const handleSendMessage = async (
  userInput: string,
  quoteData?: QuoteData | null,
  attachments?: PendingAttachment[],
  dispatchFromComposer?: MessageDispatchInput,
) => {
```

Use the final dispatch from the submit payload:

```ts
const dispatch: MessageDispatchInput = dispatchFromComposer ?? gm.resolvedTargetMode
await sendUserMessage({
  userInput,
  quoteData: quoteData ?? undefined,
  pendingAttachments: attachments,
  dispatch,
})
```

For autosend pending data, use only the persisted final dispatch, with mention routing and canonical saved-group routing already resolved by the chat page:

```ts
if (!pendingData.dispatch) {
  console.debug('Blocked pending room autosend without final MessageDispatchInput')
  return
}

const dispatch = pendingData.dispatch
await sendUserMessage({
  userInput: pendingData.initialMessage,
  pendingAttachments: pendingData.attachments,
  dispatch,
})
```

Do not parse `<@id|name>` in the room send path after this task.

- [ ] **Step 5: Update tests**

Update room-ui-store and chat-room-creation tests to assert final dispatch survives handoff:

```ts
expect(pending?.dispatch).toEqual({ mentioned_agent_ids: ['agent-a'] })
expect(pending?.targetGroup).toBeUndefined()
```

Add a saved-group handoff regression:

```ts
expect(pending?.dispatch).toEqual({
  message_target_mode: 'saved_group',
  target_group_id: 'grp-saved-123',
})
```

Update room page tests so autosend with mention dispatch calls:

```ts
expect(mockSendUserMessage.mock.calls[0][0]).toEqual(expect.objectContaining({
  dispatch: { mentioned_agent_ids: ['agent-a'] },
}))
```

Add a room page autosend saved-group regression:

```ts
expect(mockSendUserMessage.mock.calls[0][0]).toEqual(expect.objectContaining({
  dispatch: {
    message_target_mode: 'saved_group',
    target_group_id: 'grp-saved-123',
  },
}))
```

- [ ] **Step 6: Run mention and handoff tests**

Run:

```bash
npx vitest run tests/unit/components/room-chat-input-mention.test.tsx tests/unit/components/room-page-prefill.test.tsx tests/unit/hooks/useChatRoomCreation.test.ts tests/unit/hooks/useGroupManagement.test.ts tests/unit/hooks/useGroupManagement-empty-room.test.ts tests/unit/stores/room-ui-store.test.ts tests/unit/stores/scope-and-stale.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/components/room-chat-input.tsx src/components/composer/ComposerShell.tsx src/components/room-page-shell.tsx src/app/c/chat/page.tsx src/app/c/room/[id]/page.tsx src/hooks/useGroupManagement.ts src/hooks/useChatRoomCreation.ts src/stores/room-ui-store.ts tests/unit/components/room-chat-input-mention.test.tsx tests/unit/components/room-page-prefill.test.tsx tests/unit/hooks/useChatRoomCreation.test.ts tests/unit/hooks/useGroupManagement.test.ts tests/unit/hooks/useGroupManagement-empty-room.test.ts tests/unit/stores/room-ui-store.test.ts tests/unit/stores/scope-and-stale.test.ts
git commit -m "refactor: route mentions with explicit agent ids"
```

## Task 7: Full Regression and Legacy Removal Scan

**Files:**
- Modify tests found by the scan only when they assert legacy SSE or send-message behavior.

- [ ] **Step 1: Run protocol string scan**

Run:

```bash
rg -n "event_type|processing_message_id|processingMessageId|NEXT_PUBLIC_SSE_CORRELATION_COMPAT" src tests
rg -n "\\bevent\\.type\\b" src/lib/api/sse.ts src/hooks/useRoomSSE.ts src/hooks/room src/lib/types/sse.ts tests/unit/hooks/useRoomSSE.test.ts tests/unit/lib/sse-connection.test.ts
rg -n "\\buser_message\\b|\\bturn_event\\b" src/hooks src/lib tests/setup tests/fixtures tests/unit
rg -n "normalizeLegacyTargetGroup" src tests
rg -n "sendUserMessage\\(\\s*['\"]|sendUserMessage\\([^\\{][^\\n]*targetGroup|onSendMessage:\\s*\\([^)]*targetGroup" src tests
rg -n "['\"]target_group['\"]|\\btarget_group\\b" src | rg -v "target_group_id"
rg -n "['\"]target_group['\"]|\\btarget_group\\b" tests | rg -v "target_group_id" | rg -v "not\\.toHaveProperty\\([\"']target_group[\"']\\)"
rg -n "type: ['\"]processing_status['\"]|details: ['\"][^'\"]+['\"]" src/hooks/room tests/unit/hooks tests/fixtures
rg -n "\"payload\"|\\.payload|payload:" src/lib/api/sse.ts src/hooks/room src/hooks/useRoomSSE.ts src/hooks/room/useRoomSSEConnection.ts src/lib/types/sse.ts src/lib/api/room.ts tests/unit/lib/sse-types.test.ts tests/unit/lib/sse-connection.test.ts tests/unit/hooks/useRoomSSE.test.ts tests/unit/hooks/useRoomWebhook.test.ts
```

Expected:

- No `event_type` usage in room SSE code.
- No browser `event.type` usage in the room SSE path.
- Any `payload` search hits in the room SSE/API paths are limited to final `run_event.data.payload`; there is no top-level room SSE outer-protocol `payload`.
- No exact `target_group` in implementation code after excluding canonical `target_group_id`.
- No `processing_message_id` or `processingMessageId` usage.
- No known dispatch support for `user_message` or `turn_event`.
- No `normalizeLegacyTargetGroup` helper or imports remain.
- No old positional `sendUserMessage('...')`, `targetGroup` send calls, or adapter signatures remain.
- Any `user_message` or `turn_event` hits in tests are limited to negative regressions that assert those legacy protocols are rejected.
- Any exact `target_group` hits in tests are limited to explicit absence assertions such as `not.toHaveProperty('target_group')`; envelope negative tests should use a computed legacy key so the scan does not hide real legacy request shapes. If the second `target_group` test scan prints anything, inspect and either remove the legacy assertion or justify it in the test name.
- Any string `details` search hit near `processing_status` fixtures/tests is limited to the explicit negative regression that proves legacy string `processing_status.details` is debug-logged and ignored. Unrelated non-SSE `details` fields are outside this scan.

- [ ] **Step 2: Run focused unit suite**

Run:

```bash
npx vitest run \
  tests/unit/lib/sse-types.test.ts \
  tests/unit/lib/sse-connection.test.ts \
  tests/unit/hooks/useRoomSSE.test.ts \
  tests/unit/hooks/useRoomWebhook.test.ts \
  tests/unit/hooks/hitl-sse-handlers.test.ts \
  tests/unit/hooks/room-lifecycle.test.ts \
  tests/unit/lib/room-api.test.ts \
  tests/unit/hooks/double-send-guard.test.ts \
  tests/unit/components/room-chat-input-mention.test.tsx \
  tests/unit/components/room-page-prefill.test.tsx \
  tests/unit/hooks/useChatRoomCreation.test.ts \
  tests/unit/hooks/useGroupManagement.test.ts \
  tests/unit/hooks/useGroupManagement-empty-room.test.ts \
  tests/unit/stores/room-ui-store.test.ts \
  tests/unit/stores/scope-and-stale.test.ts \
  tests/unit/stores/streaming-store.test.ts \
  tests/unit/stores/fixture-type-safety.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run typecheck/build**

Run:

```bash
npx tsc --noEmit
npm run build
```

Expected: both commands complete successfully.

- [ ] **Step 4: Run full unit tests**

Run:

```bash
npm run test
```

Expected: PASS.

- [ ] **Step 5: Manual browser verification**

Run:

```bash
npm run dev
```

Open a room, send these messages, and verify:

- No mention: selected Room Default sends `message_target_mode: "room_default"` and `client_request_id`.
- No mention: selected All Agents sends `message_target_mode: "all_agents"` and `client_request_id`.
- No mention: selected saved group sends `message_target_mode: "saved_group"`, `target_group_id`, and `client_request_id`.
- Explicit mention sends `mentioned_agent_ids` and does not send `message_target_mode` or `target_group_id`.
- Incoming `connected` and `heartbeat` frames with `data` do not create timeline messages.
- Incoming unknown top-level SSE types are debug-logged and ignored.
- Incoming `processing_status.details` objects render useful work-log text.

- [ ] **Step 6: Final commit**

```bash
git add src tests
git commit -m "test: verify final room sse and send protocols"
```

## Self-Review Checklist

- [ ] Final SSE envelope is top-level `type`, `timestamp`, `room_id`, `data`.
- [ ] The stream reader never reads named SSE `event:` metadata and never references browser `event.type`.
- [ ] `event_type` and outer `payload` are not used as room SSE wire protocol.
- [ ] `data` is required at the type boundary; connected and heartbeat fixtures include `data`.
- [ ] Unknown `frame.type` logs debug and returns without throwing.
- [ ] Supported event list includes all final types: `connected`, `heartbeat`, `processing_status`, `run_event`, `task_submitted`, `task_update`, `artifact_update`, `agent_response`, `agent_response_partial`, `error`, `hitl_input_requested`, `hitl_status_update`, `cancellation`, `hub_agent_event`, `debate_round`.
- [ ] Processing statuses include `queued`, `processing`, `awaiting_input`, `completed`, `failed`, `canceled`, `rejected`, `rate_limited`, and `error`.
- [ ] `processing_status.details` is handled only as object/null.
- [ ] Send-message requests always include `client_request_id`.
- [ ] Send-message requests never include legacy `target_group`.
- [ ] Mention routing sends only `mentioned_agent_ids`; non-mention routing sends only canonical `message_target_mode`/`target_group_id`.
- [ ] Active-turn state is correlated by `client_request_id`, `message_id`, `related_message_id`, and SSE events, not by `room.processing_message_id`.
