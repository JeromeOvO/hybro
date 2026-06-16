import { useEffect, useRef, useCallback, useState } from 'react'
import { SSEConnection } from '@/lib/api/sse'
import type { SSECloseReason } from '@/lib/api/sse'
import type { AnySSEFrame } from '@/lib/types/sse'

interface UseRoomSSEOptions {
  roomId: string
  enabled?: boolean
  getToken?: () => Promise<string | null>
  onMessage?: (message: AnySSEFrame) => void
  onConnectionChange?: (connected: boolean) => void
}

const RESURRECT_DELAY_MS = 45_000

export function useRoomSSE({ roomId, enabled = true, getToken, onMessage, onConnectionChange }: UseRoomSSEOptions) {
  const [connected, setConnected] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const connectionRef = useRef<SSEConnection | null>(null)
  const resurrectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  
  const onMessageRef = useRef(onMessage)
  const onConnectionChangeRef = useRef(onConnectionChange)
  const getTokenRef = useRef(getToken)
  
  useEffect(() => {
    onMessageRef.current = onMessage
  }, [onMessage])
  
  useEffect(() => {
    onConnectionChangeRef.current = onConnectionChange
  }, [onConnectionChange])
  
  useEffect(() => {
    getTokenRef.current = getToken
  }, [getToken])

  const handleMessage = useCallback((message: AnySSEFrame) => {
    onMessageRef.current?.(message)
  }, [])

  const handleConnectionChange = useCallback((isConnected: boolean) => {
    setConnected(isConnected)
    onConnectionChangeRef.current?.(isConnected)
  }, [])

  const clearResurrectTimer = useCallback(() => {
    if (resurrectTimerRef.current) {
      clearTimeout(resurrectTimerRef.current)
      resurrectTimerRef.current = null
    }
  }, [])

  const connect = useCallback(async () => {
    if (!enabled || !roomId || connectionRef.current?.isConnected()) {
      return
    }

    clearResurrectTimer()

    try {
      setConnecting(true)
      setError(null)

      if (connectionRef.current) {
        connectionRef.current.disconnect()
      }

      connectionRef.current = new SSEConnection({
        roomId,
        getToken: () => getTokenRef.current?.() ?? Promise.resolve(null),
        onMessage: handleMessage,
        onOpen: () => {
          clearResurrectTimer()
          handleConnectionChange(true)
          setConnecting(false)
        },
        onError: () => {
          setError('Connection error')
          handleConnectionChange(false)
          setConnecting(false)
        },
        onClose: (reason: SSECloseReason) => {
          handleConnectionChange(false)
          setConnecting(false)

          if (reason === 'permanent-failure') {
            connectionRef.current = null
            clearResurrectTimer()
            resurrectTimerRef.current = setTimeout(() => {
              // Re-read enabled/roomId from the closure at timer-fire time.
              // connect() has its own guards, so stale calls are harmless.
              connectRef.current()
            }, RESURRECT_DELAY_MS)
          }
        }
      })

      await connectionRef.current.connect()
    } catch (error) {
      console.error('❌ Failed to connect SSE:', error)
      setError(error instanceof Error ? error.message : 'Connection failed')
      setConnecting(false)
      handleConnectionChange(false)
    }
  }, [roomId, enabled, handleMessage, handleConnectionChange, clearResurrectTimer])

  // Stable ref to `connect` so the resurrect timer can call the latest version
  const connectRef = useRef(connect)
  useEffect(() => {
    connectRef.current = connect
  }, [connect])

  const disconnect = useCallback(() => {
    clearResurrectTimer()
    if (connectionRef.current) {
      connectionRef.current.disconnect()
      connectionRef.current = null
    }
    handleConnectionChange(false)
    setConnecting(false)
    setError(null)
  }, [handleConnectionChange, clearResurrectTimer])

  useEffect(() => {
    if (enabled && roomId) {
      if (connectionRef.current) {
        connectionRef.current.disconnect()
        connectionRef.current = null
      }
      connect()
    } else {
      disconnect()
    }

    return () => {
      clearResurrectTimer()
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
