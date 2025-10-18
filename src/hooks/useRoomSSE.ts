import { useEffect, useRef, useCallback, useState } from 'react'
import { SSEConnection, SSEMessage } from '@/lib/api/sse'

interface UseRoomSSEOptions {
  roomId: string
  enabled?: boolean
  onMessage?: (message: SSEMessage) => void
  onConnectionChange?: (connected: boolean) => void
}

export function useRoomSSE({ roomId, enabled = true, onMessage, onConnectionChange }: UseRoomSSEOptions) {
  const [connected, setConnected] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const connectionRef = useRef<SSEConnection | null>(null)

  const handleMessage = useCallback((message: SSEMessage) => {
    console.log('📨 SSE Hook received message:', message)
    onMessage?.(message)
  }, [onMessage])

  const handleConnectionChange = useCallback((isConnected: boolean) => {
    setConnected(isConnected)
    onConnectionChange?.(isConnected)
  }, [onConnectionChange])

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

      // Create new connection
      connectionRef.current = new SSEConnection({
        roomId,
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

  // Auto connect/disconnect based on enabled and roomId
  useEffect(() => {
    if (enabled && roomId) {
      connect()
    } else {
      disconnect()
    }

    // Cleanup on unmount or dependency change
    return () => {
      disconnect()
    }
  }, [roomId, enabled]) // Note: not including connect/disconnect to avoid infinite loops

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (connectionRef.current) {
        connectionRef.current.disconnect()
      }
    }
  }, [])

  return {
    connected,
    connecting,
    error,
    connect,
    disconnect,
    isConnected: () => connectionRef.current?.isConnected() ?? false
  }
}