// SSE-specific types matching the backend implementation

// Re-export TaskState from the official A2A JS SDK — single source of truth.
// This replaces the hand-maintained union type that previously used snake_case
// variants ("input_required", "auth_required") which diverged from the A2A spec's
// kebab-case values ("input-required", "auth-required").
export type { TaskState } from '@a2a-js/sdk'
import type { TaskState } from '@a2a-js/sdk'

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
  | 'hitl_request'
  | 'hitl_response'
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
  'hitl_request',
  'hitl_response',
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
  message_id: string
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
  step_number?: number | null
  total_steps?: number | null
  expires_at?: string | null
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
  hitl_request: SSEFrame<'hitl_request', HITLInputRequestedData>
  hitl_response: SSEFrame<'hitl_response', HITLStatusUpdateData>
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

export interface SSEConnectionStatus {
  room_id: string
  active_connections: number
  status: 'active' | 'no_connections'
}

// All possible A2A task states — sourced from @a2a-js/sdk TaskState type.
// The SDK uses kebab-case ("input-required", "auth-required") per the A2A spec.
// Note: The SDK also includes "unknown" which we handle in our helper functions.

// Named constants for individual TaskState values.
// The SDK only exports the TaskState *type*, not a constant object, so we
// provide one here so consumers never need bare string literals.
export const TASK_STATE = {
  SUBMITTED: "submitted",
  WORKING: "working",
  INPUT_REQUIRED: "input-required",
  AUTH_REQUIRED: "auth-required",
  COMPLETED: "completed",
  CANCELED: "canceled",
  FAILED: "failed",
  REJECTED: "rejected",
  UNKNOWN: "unknown",
} as const satisfies Record<string, TaskState>

// States that are still in progress
export const PENDING_STATES: TaskState[] = [TASK_STATE.SUBMITTED, TASK_STATE.WORKING]

// States that require user action
export const INTERACTIVE_STATES: TaskState[] = [TASK_STATE.INPUT_REQUIRED, TASK_STATE.AUTH_REQUIRED]

// States that indicate task is done
export const TERMINAL_STATES: TaskState[] = [TASK_STATE.COMPLETED, TASK_STATE.FAILED, TASK_STATE.CANCELED, TASK_STATE.REJECTED]

// States that indicate task ended unsuccessfully
export const FAILURE_STATES: TaskState[] = [TASK_STATE.FAILED, TASK_STATE.REJECTED, TASK_STATE.CANCELED]

export function isTerminalState(state: TaskState): boolean {
  return TERMINAL_STATES.includes(state)
}

export function isFailureState(state: TaskState): boolean {
  return FAILURE_STATES.includes(state)
}

export function isInteractiveState(state: TaskState): boolean {
  return INTERACTIVE_STATES.includes(state)
}

export function isPendingState(state: TaskState): boolean {
  return PENDING_STATES.includes(state)
}

// Task submitted event data
export interface TaskSubmittedEvent {
  type: "task_submitted"
  data: {
    message_id: string
    task_id: string
    agent_name: string
    agent_id?: string
    status: "submitted" | "working"
  }
}

// Task update event data
export interface TaskUpdateEvent {
  type: "task_update"
  data: {
    message_id: string
    status: TaskState
    content?: string          // Present if completed
    error?: string            // Present if failed/rejected/canceled
    requires_input?: boolean  // True if input-required
    requires_auth?: boolean   // True if auth-required
    status_message?: string   // Human-readable status from agent
    agent_name?: string
    agent_id?: string
    task_content?: string     // The task description being processed
  }
}

// --- HITL (Human-in-the-Loop) Types ---

export type HITLPromptType = 'text' | 'choice' | 'confirmation'
export type HITLStatus = 'pending' | 'responded' | 'expired' | 'canceled' | 'error'

// --- Internal Processing Status (SSE processing_status events) ---

export const PROCESSING_STATUS = {
  QUEUED: "queued",
  PROCESSING: "processing",
  AWAITING_INPUT: "awaiting_input",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELED: "canceled",
  REJECTED: "rejected",
  RATE_LIMITED: "rate_limited",
  ERROR: "error",
} as const satisfies Record<string, ProcessingStatus>

// Statuses that mean processing is done (clear spinner)
export const PROCESSING_DONE_STATUSES: ProcessingStatus[] = [
  PROCESSING_STATUS.COMPLETED, PROCESSING_STATUS.CANCELED, PROCESSING_STATUS.FAILED, PROCESSING_STATUS.REJECTED,
  PROCESSING_STATUS.RATE_LIMITED, PROCESSING_STATUS.ERROR,
]

export function isProcessingDone(status: ProcessingStatus): boolean {
  return PROCESSING_DONE_STATUSES.includes(status)
}
