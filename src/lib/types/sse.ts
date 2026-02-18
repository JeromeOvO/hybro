// SSE-specific types matching the backend implementation

// Re-export TaskState from the official A2A JS SDK — single source of truth.
// This replaces the hand-maintained union type that previously used snake_case
// variants ("input_required", "auth_required") which diverged from the A2A spec's
// kebab-case values ("input-required", "auth-required").
export type { TaskState } from '@a2a-js/sdk'
import type { TaskState } from '@a2a-js/sdk'
export interface SSEMessage {
  type: 'connected' | 'user_message' | 'agent_response' | 'processing_status' | 'heartbeat' | 'error' | 'task_submitted' | 'task_update'
  room_id: string
  timestamp: string
  data?: {
    message_id?: string
    user_id?: string
    agent_id?: string
    content?: string
    related_message_id?: string
    status?: string // "processing", "completed", "canceled", "failed", "rate_limited"
    details?: string
    // Error-specific fields
    error?: string
    error_type?: string // "rate_limit_exceeded"
    // Rate limit specific fields
    retry_after_seconds?: number
    user_requests_used?: number
    user_requests_limit?: number
    system_requests_used?: number
    system_requests_limit?: number
    // Task-specific fields (for task_submitted and task_update events)
    task_id?: string
    agent_name?: string
    requires_input?: boolean
    requires_auth?: boolean
    status_message?: string
    created_at?: string // Task creation timestamp for consistent ordering
    // Workflow step tracking
    step_number?: number // Current step number in the workflow (1-indexed)
    total_steps?: number // Total number of steps in the workflow
    // Task content (the description of what the agent is working on)
    task_content?: string
  }
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

// --- Internal Processing Status (SSE processing_status events) ---

export type ProcessingStatus =
  | "processing"
  | "completed"
  | "canceled"
  | "failed"
  | "rejected"
  | "rate_limited"

export const PROCESSING_STATUS = {
  PROCESSING: "processing",
  COMPLETED: "completed",
  CANCELED: "canceled",
  FAILED: "failed",
  REJECTED: "rejected",
  RATE_LIMITED: "rate_limited",
} as const

// Statuses that mean processing is done (clear spinner)
export const PROCESSING_DONE_STATUSES: ProcessingStatus[] = [
  PROCESSING_STATUS.COMPLETED, PROCESSING_STATUS.CANCELED, PROCESSING_STATUS.FAILED, PROCESSING_STATUS.REJECTED
]

export function isProcessingDone(status: ProcessingStatus): boolean {
  return PROCESSING_DONE_STATUSES.includes(status)
}
