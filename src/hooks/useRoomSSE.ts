import { useEffect, useRef, useCallback, useState } from 'react'
import { SSEConnection, SSEMessage } from '@/lib/api/sse'

interface UseRoomSSEOptions {
  roomId: string
  enabled?: boolean
  getToken?: () => Promise<string | null>
  onMessage?: (message: SSEMessage) => void
  onConnectionChange?: (connected: boolean) => void
}

export function useRoomSSE({ roomId, enabled = true, getToken, onMessage, onConnectionChange }: UseRoomSSEOptions) {
  const [connected, setConnected] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const connectionRef = useRef<SSEConnection | null>(null)
  
  // Use refs for callbacks to avoid recreating connect/disconnect and triggering reconnections
  const onMessageRef = useRef(onMessage)
  const onConnectionChangeRef = useRef(onConnectionChange)
  const getTokenRef = useRef(getToken)
  
  // Keep refs up to date without triggering reconnections
  useEffect(() => {
    onMessageRef.current = onMessage
  }, [onMessage])
  
  useEffect(() => {
    onConnectionChangeRef.current = onConnectionChange
  }, [onConnectionChange])
  
  useEffect(() => {
    getTokenRef.current = getToken
  }, [getToken])

  // Stable handlers that use refs - won't change and won't trigger reconnections
  const handleMessage = useCallback((message: SSEMessage) => {
    console.log('📨 SSE Hook received message:', message)
    onMessageRef.current?.(message)
  }, [])

  const handleConnectionChange = useCallback((isConnected: boolean) => {
    setConnected(isConnected)
    onConnectionChangeRef.current?.(isConnected)
  }, [])

  const connect = useCallback(async () => {
    if (!enabled || !roomId || connectionRef.current?.isConnected()) {
      return
    }

    try {
      setConnecting(true)
      setError(null)

      // Disconnect existing connection
      if (connectionRef.current) {
        connectionRef.current.disconnect()
      }

      // Create new connection using ref for getToken
      connectionRef.current = new SSEConnection({
        roomId,
        getToken: () => getTokenRef.current?.() ?? Promise.resolve(null),
        onMessage: handleMessage,
        onOpen: () => {
          console.log('✅ SSE connected')
          handleConnectionChange(true)
          setConnecting(false)
        },
        onError: (event) => {
          console.error('❌ SSE error:', event)
          setError('Connection error')
          handleConnectionChange(false)
          setConnecting(false)
        },
        onClose: () => {
          console.log('🔌 SSE disconnected')
          handleConnectionChange(false)
          setConnecting(false)
        }
      })

      await connectionRef.current.connect()
    } catch (error) {
      console.error('❌ Failed to connect SSE:', error)
      setError(error instanceof Error ? error.message : 'Connection failed')
      setConnecting(false)
      handleConnectionChange(false)
    }
  }, [roomId, enabled, handleMessage, handleConnectionChange])

  const disconnect = useCallback(() => {
    if (connectionRef.current) {
      connectionRef.current.disconnect()
      connectionRef.current = null
    }
    handleConnectionChange(false)
    setConnecting(false)
    setError(null)
  }, [handleConnectionChange])

  // Auto connect/disconnect based on enabled and roomId ONLY
  // Removed connect/disconnect from deps to prevent reconnection loops
  useEffect(() => {
    if (enabled && roomId) {
      // Ensure previous connection is closed before creating a new one
      if (connectionRef.current) {
        connectionRef.current.disconnect()
        connectionRef.current = null
      }
      connect()
    } else {
      disconnect()
    }

    // Cleanup on unmount or when roomId/enabled change
    return () => {
      if (connectionRef.current) {
        connectionRef.current.disconnect()
        connectionRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, enabled])

  return {
    connected,
    connecting,
    error,
    connect,
    disconnect,
    isConnected: () => connectionRef.current?.isConnected() ?? false
  }
}