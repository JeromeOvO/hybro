import type { SSEConnectionStatus } from '@/lib/types/sse'
import { getApiUrl } from '../utils'
import { getClientAuthHeaders } from '../auth'

const API_BASE_URL = getApiUrl('sse')

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
    status?: string // "processing", "completed", "failed"
    details?: string
  }
}

export interface SSEConnectionOptions {
  roomId: string
  getToken?: () => Promise<string | null>
  onMessage?: (message: SSEMessage) => void
  onError?: (error: Event) => void
  onOpen?: (event: Event) => void
  onClose?: (event: Event) => void
}

export class SSEConnection {
  private eventSource: EventSource | null = null
  private roomId: string
  private options: SSEConnectionOptions
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5
  private reconnectDelay = 1000
  private isManualClose = false

  constructor(options: SSEConnectionOptions) {
    this.roomId = options.roomId
    this.options = options
  }

  async connect(): Promise<void> {
    return new Promise(async (resolve, reject) => {
      try {
        this.isManualClose = false
        
        // Get auth token if available
        const token = this.options.getToken ? await this.options.getToken() : null
        
        // Build URL with auth token as query param for SSE (EventSource doesn't support custom headers)
        let url = `${API_BASE_URL}/room/${this.roomId}/stream`
        if (token) {
          url += `?token=${encodeURIComponent(token)}`
        }
        
        console.log('🔗 Connecting to SSE:', url.replace(/token=[^&]+/, 'token=***'))
        
        this.eventSource = new EventSource(url)

        this.eventSource.onopen = (event) => {
          console.log('✅ SSE connection opened for room:', this.roomId)
          this.reconnectAttempts = 0
          this.options.onOpen?.(event)
          resolve()
        }

        this.eventSource.onmessage = (event) => {
          try {
            const message: SSEMessage = JSON.parse(event.data)
            console.log('📨 SSE message received:', message)
            
            // Handle heartbeat messages silently
            if (message.type === 'heartbeat') {
              return
            }
            
            this.options.onMessage?.(message)
          } catch (error) {
            console.error('❌ Failed to parse SSE message:', error, event.data)
          }
        }

        this.eventSource.onerror = (event) => {
          console.error('❌ SSE connection error for room:', this.roomId, event)
          this.options.onError?.(event)
          
          // Auto-reconnect if not manually closed
          if (!this.isManualClose && this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++
            console.log(`🔄 Attempting to reconnect SSE (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`)
            
            setTimeout(() => {
              if (!this.isManualClose) {
                this.connect().catch(console.error)
              }
            }, this.reconnectDelay * this.reconnectAttempts)
          } else if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.error('❌ Max SSE reconnection attempts reached')
            reject(new Error('Max reconnection attempts reached'))
          }
        }

      } catch (error) {
        console.error('❌ Failed to create SSE connection:', error)
        reject(error)
      }
    })
  }

  disconnect(): void {
    this.isManualClose = true
    if (this.eventSource) {
      this.eventSource.close()
      this.eventSource = null
      console.log('🔌 SSE connection manually closed for room:', this.roomId)
    }
  }

  isConnected(): boolean {
    return this.eventSource?.readyState === EventSource.OPEN
  }

  getConnectionState(): number {
    return this.eventSource?.readyState ?? EventSource.CLOSED
  }
}

// Get SSE connection status
export async function getSSEStatus(
  roomId: string,
  getToken?: () => Promise<string | null>
): Promise<SSEConnectionStatus> {
  const url = `${API_BASE_URL}/room/${roomId}/status`
  
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(url, { headers })
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return await response.json() as SSEConnectionStatus
}