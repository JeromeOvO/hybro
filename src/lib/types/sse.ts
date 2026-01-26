// SSE-specific types matching the backend implementation
export interface SSEMessage {
  type: 'connected' | 'user_message' | 'agent_response' | 'processing_status' | 'heartbeat' | 'error'
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
  }
}

export interface SSEConnectionStatus {
  room_id: string
  active_connections: number
  status: 'active' | 'no_connections'
}
