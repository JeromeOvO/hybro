import type { SSEConnectionStatus, SSEMessage } from '@/lib/types/sse'
import { getApiUrl } from '../utils'
import { getClientAuthHeaders } from '../auth'

const API_BASE_URL = getApiUrl('sse')

// Re-export SSEMessage for convenience
export type { SSEMessage }

export interface SSEConnectionOptions {
  roomId: string
  getToken?: () => Promise<string | null>
  onMessage?: (message: SSEMessage) => void
  onError?: (error: Event) => void
  onOpen?: (event: Event) => void
  onClose?: (event: Event) => void
}

// Connection state constants (mirrors EventSource.readyState values)
export const SSE_STATE = {
  CONNECTING: 0,
  OPEN: 1,
  CLOSED: 2,
} as const

const { CONNECTING, OPEN, CLOSED } = SSE_STATE

export class SSEConnection {
  private abortController: AbortController | null = null
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null
  private connectionState: number = CLOSED
  private roomId: string
  private options: SSEConnectionOptions
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5
  private reconnectDelay = 1000
  private isManualClose = false
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private connectCancelled = false

  constructor(options: SSEConnectionOptions) {
    this.roomId = options.roomId
    this.options = options
  }

  async connect(): Promise<void> {
    return new Promise(async (resolve, reject) => {
      try {
        this.isManualClose = false
        this.connectCancelled = false
        this.connectionState = CONNECTING

        // Get auth token if available
        const token = this.options.getToken ? await this.options.getToken() : null

        // If a disconnect was requested while awaiting token, abort
        if (this.isManualClose || this.connectCancelled) {
          this.connectionState = CLOSED
          return resolve()
        }

        // Build URL without token in query string (security fix for issue 2.1)
        const url = `${API_BASE_URL}/room/${this.roomId}/stream`

        // Send JWT via Authorization header instead of URL query parameter
        const headers: Record<string, string> = {
          'Accept': 'text/event-stream',
          'Cache-Control': 'no-cache',
        }
        if (token) {
          headers['Authorization'] = `Bearer ${token}`
        }

        this.abortController = new AbortController()

        const response = await fetch(url, {
          method: 'GET',
          headers,
          signal: this.abortController.signal,
        })

        if (!response.ok) {
          throw new Error(`SSE connection failed: HTTP ${response.status}`)
        }

        if (!response.body) {
          throw new Error('SSE response has no body')
        }

        // Connection established
        this.connectionState = OPEN
        this.reconnectAttempts = 0
        this.options.onOpen?.(new Event('open'))
        resolve()

        // Start reading the stream (runs until disconnect or error)
        this.reader = response.body.getReader()
        await this.readStream(this.reader)

      } catch (error) {
        // Handle abort (from disconnect())
        if (error instanceof DOMException && error.name === 'AbortError') {
          this.connectionState = CLOSED
          return resolve()
        }

        this.connectionState = CLOSED
        this.options.onError?.(new Event('error'))

        // If we're intentionally closing or cancelled, do not attempt reconnect
        if (this.isManualClose || this.connectCancelled) {
          return resolve()
        }

        this.attemptReconnect(reject)
      }
    })
  }

  private async readStream(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<void> {
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()

        if (done) {
          break
        }

        buffer += decoder.decode(value, { stream: true })
        const { messages, remainder } = this.processSSEBuffer(buffer)
        buffer = remainder

        for (const data of messages) {
          try {
            const message: SSEMessage = JSON.parse(data)

            // Handle heartbeat messages silently
            if (message.type === 'heartbeat') {
              continue
            }

            this.options.onMessage?.(message)
          } catch (parseError) {
            console.error('Failed to parse SSE message:', parseError, data)
          }
        }
      }
    } catch (error) {
      // AbortError is expected during disconnect — don't treat as a connection error
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }
      // Other read errors fall through to finally block
    } finally {
      this.connectionState = CLOSED

      if (!this.isManualClose && !this.connectCancelled) {
        // Stream ended unexpectedly (server closed or network error) — attempt reconnect
        this.options.onError?.(new Event('error'))
        this.attemptReconnect()
      } else {
        this.options.onClose?.(new Event('close'))
      }
    }
  }

  private processSSEBuffer(buffer: string): { messages: string[]; remainder: string } {
    const messages: string[] = []
    const blocks = buffer.split('\n\n')

    // Last element is incomplete (no trailing \n\n) — keep as remainder
    const remainder = blocks.pop() ?? ''

    for (const block of blocks) {
      if (!block.trim()) continue

      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('data: ')) {
          data += (data ? '\n' : '') + line.slice(6)
        } else if (line.startsWith('data:')) {
          data += (data ? '\n' : '') + line.slice(5)
        }
        // Note: bare "data:" (no value) is treated as empty string per SSE spec,
        // handled by the slice(5) branch above. Comment lines (:), id:, event:,
        // retry: are ignored — not used by our backend.
      }

      if (data) {
        messages.push(data)
      }
    }

    return { messages, remainder }
  }

  private attemptReconnect(reject?: (reason: Error) => void): void {
    if (this.isManualClose || this.connectCancelled) return

    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      this.reconnectAttempts++

      // Linear backoff: delay = base * attempt (1s, 2s, 3s, 4s, 5s)
      const delay = this.reconnectDelay * this.reconnectAttempts

      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer)
      }
      this.reconnectTimer = setTimeout(() => {
        if (!this.isManualClose && !this.connectCancelled) {
          this.connect().catch(console.error)
        }
      }, delay)
    } else {
      // Notify consumer that reconnection failed permanently
      this.options.onError?.(new Event('error'))
      this.options.onClose?.(new Event('close'))
      reject?.(new Error('Max reconnection attempts reached'))
    }
  }

  disconnect(): void {
    this.isManualClose = true
    this.connectCancelled = true
    this.connectionState = CLOSED

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }

    if (this.abortController) {
      this.abortController.abort()
      this.abortController = null
    }

    if (this.reader) {
      this.reader.cancel().catch(() => { /* reader already released or stream closed */ })
      this.reader = null
    }
  }

  isConnected(): boolean {
    return this.connectionState === OPEN
  }

  getConnectionState(): number {
    return this.connectionState
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

// Cancel message processing
export async function cancelMessage(
  messageId: string,
  getToken?: () => Promise<string | null>
): Promise<{ success: boolean; message_id: string; message: string }> {
  const url = `${API_BASE_URL}/message/${messageId}/cancel`
  
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(url, {
    method: 'POST',
    headers
  })
  
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  
  return await response.json()
}
