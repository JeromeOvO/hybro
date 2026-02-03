// SSE-specific types matching the backend implementation
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
    status?: string // "processing", "completed", "cancelled", "failed", "rate_limited"
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
    internal_id?: string
    task_id?: string
    agent_name?: string
    requires_input?: boolean
    requires_auth?: boolean
    status_message?: string
    created_at?: string // Task creation timestamp for consistent ordering
  }
}

export interface SSEConnectionStatus {
  room_id: string
  active_connections: number
  status: 'active' | 'no_connections'
}

// All possible A2A task states (mirrors backend a2a.types.TaskState)
export type TaskState = 
  | "submitted" 
  | "working" 
  | "completed" 
  | "failed" 
  | "canceled"
  | "input_required"
  | "rejected"
  | "auth_required"

// States that are still in progress
export const PENDING_STATES: TaskState[] = ["submitted", "working"]

// States that require user action
export const INTERACTIVE_STATES: TaskState[] = ["input_required", "auth_required"]

// States that indicate task is done
export const TERMINAL_STATES: TaskState[] = ["completed", "failed", "canceled", "rejected"]

export function isTerminalState(state: TaskState): boolean {
  return TERMINAL_STATES.includes(state)
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
    internal_id: string
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
    internal_id: string
    status: TaskState
    content?: string          // Present if completed
    error?: string            // Present if failed/rejected/canceled
    requires_input?: boolean  // True if input_required
    requires_auth?: boolean   // True if auth_required
    status_message?: string   // Human-readable status from agent
    agent_name?: string
    agent_id?: string
  }
}
