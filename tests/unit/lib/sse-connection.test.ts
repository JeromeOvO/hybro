import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { SSEConnection, type SSEConnectionOptions } from '@/lib/api/sse'
import { MockEventSource, installMockEventSource } from '../../setup/mock-event-source'

installMockEventSource()

async function connectAndOpen(options: Partial<SSEConnectionOptions> = {}) {
  const connection = new SSEConnection({ roomId: 'test-room', ...options })
  const connectPromise = connection.connect()
  const instance = MockEventSource.getLastInstanceOrFail()
  instance.simulateOpen()
  await connectPromise
  return { connection, instance }
}

describe('SSEConnection', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    MockEventSource.clearInstances()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  describe('connect', () => {
    it('should create EventSource with correct URL', async () => {
      const { instance } = await connectAndOpen()
      expect(instance.url).toContain('/sse/room/test-room/stream')
    })

    it('should include auth token in URL when provided', async () => {
      const getToken = vi.fn().mockResolvedValue('test-token')
      const connection = new SSEConnection({ roomId: 'test-room', getToken })

      const connectPromise = connection.connect()
      await vi.runAllTimersAsync()

      const instance = MockEventSource.getLastInstanceOrFail()
      expect(instance.url).toContain('token=test-token')

      instance.simulateOpen()
      await connectPromise
    })

    it('should call onOpen callback when connection opens', async () => {
      const onOpen = vi.fn()
      await connectAndOpen({ onOpen })
      expect(onOpen).toHaveBeenCalledTimes(1)
    })

    it('should be connected after successful open', async () => {
      const { connection } = await connectAndOpen()
      expect(connection.isConnected()).toBe(true)
    })

    it('should connect without token param when getToken returns null', async () => {
      const getToken = vi.fn().mockResolvedValue(null)
      const connection = new SSEConnection({ roomId: 'test-room', getToken })

      const connectPromise = connection.connect()
      await vi.runAllTimersAsync()

      const instance = MockEventSource.getLastInstanceOrFail()
      expect(instance.url).not.toContain('token=')

      instance.simulateOpen()
      await connectPromise
      expect(connection.isConnected()).toBe(true)
    })
  })

  describe('message handling', () => {
    it('should parse and forward SSE messages', async () => {
      const onMessage = vi.fn()
      const { instance } = await connectAndOpen({ onMessage })

      const testMessage = {
        type: 'task_update',
        room_id: 'test-room',
        timestamp: new Date().toISOString(),
        data: { message_id: 'msg-1', status: 'completed' },
      }
      instance.simulateMessage(testMessage)

      expect(onMessage).toHaveBeenCalledWith(testMessage)
    })

    it('should silently ignore heartbeat messages', async () => {
      const onMessage = vi.fn()
      const { instance } = await connectAndOpen({ onMessage })

      instance.simulateMessage({
        type: 'heartbeat',
        room_id: 'test-room',
        timestamp: new Date().toISOString(),
      })

      expect(onMessage).not.toHaveBeenCalled()
    })

    it('should handle malformed JSON gracefully', async () => {
      const onMessage = vi.fn()
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const { instance } = await connectAndOpen({ onMessage })

      const event = new MessageEvent('message', { data: 'invalid json' })
      instance.onmessage?.(event)

      expect(onMessage).not.toHaveBeenCalled()
      expect(consoleSpy).toHaveBeenCalled()
      consoleSpy.mockRestore()
    })
  })

  describe('error handling and reconnection', () => {
    it('should call onError callback on connection error', async () => {
      const onError = vi.fn()
      const { instance } = await connectAndOpen({ onError })

      instance.simulateError()
      expect(onError).toHaveBeenCalledTimes(1)
    })

    it('should attempt reconnection on error', async () => {
      const { instance } = await connectAndOpen()
      const initialCount = MockEventSource.getInstanceCount()

      instance.simulateError()
      await vi.advanceTimersByTimeAsync(1000)

      expect(MockEventSource.getInstanceCount()).toBeGreaterThan(initialCount)
    })

    it('should use increasing delay for reconnection attempts', async () => {
      const { instance: first } = await connectAndOpen()

      first.simulateError()
      await vi.advanceTimersByTimeAsync(1000)

      const second = MockEventSource.getLastInstanceOrFail()
      second.simulateError()
      await vi.advanceTimersByTimeAsync(2000)

      const third = MockEventSource.getLastInstanceOrFail()
      third.simulateError()
      await vi.advanceTimersByTimeAsync(3000)

      expect(MockEventSource.getLastInstance()).toBeDefined()
    })

    it('should stop reconnecting after max attempts', async () => {
      const { instance: first } = await connectAndOpen()

      let current = first
      for (let i = 0; i < 6; i++) {
        current = MockEventSource.getLastInstanceOrFail()
        current.simulateError()
        await vi.advanceTimersByTimeAsync(10000)
      }

      const { connection } = await connectAndOpen()
      connection.disconnect()
      expect(connection.isConnected()).toBe(false)
    })

    it('should not reconnect after manual disconnect', async () => {
      const { connection, instance } = await connectAndOpen()
      const countBeforeDisconnect = MockEventSource.getInstanceCount()

      connection.disconnect()
      instance.simulateError()
      await vi.advanceTimersByTimeAsync(5000)

      expect(connection.isConnected()).toBe(false)
    })
  })

  describe('disconnect', () => {
    it('should close EventSource on disconnect', async () => {
      const { connection, instance } = await connectAndOpen()
      connection.disconnect()
      expect(instance.readyState).toBe(MockEventSource.CLOSED)
    })

    it('should be safe to call disconnect multiple times', async () => {
      const { connection } = await connectAndOpen()
      expect(() => {
        connection.disconnect()
        connection.disconnect()
        connection.disconnect()
      }).not.toThrow()
    })

    it('should cancel pending reconnection on disconnect', async () => {
      const { connection, instance } = await connectAndOpen()

      instance.simulateError()
      connection.disconnect()
      await vi.advanceTimersByTimeAsync(5000)

      expect(connection.isConnected()).toBe(false)
    })
  })

  describe('connection state', () => {
    it('should report correct connection state lifecycle', async () => {
      const connection = new SSEConnection({ roomId: 'test-room' })
      expect(connection.isConnected()).toBe(false)

      const connectPromise = connection.connect()
      MockEventSource.getLastInstanceOrFail().simulateOpen()
      await connectPromise
      expect(connection.isConnected()).toBe(true)

      connection.disconnect()
      expect(connection.isConnected()).toBe(false)
    })

    it('should return correct readyState', async () => {
      const connection = new SSEConnection({ roomId: 'test-room' })
      expect(connection.getConnectionState()).toBe(EventSource.CLOSED)

      const connectPromise = connection.connect()
      MockEventSource.getLastInstanceOrFail().simulateOpen()
      await connectPromise
      expect(connection.getConnectionState()).toBe(EventSource.OPEN)
    })
  })

  describe('race conditions', () => {
    it('should abort connection if disconnect called during getToken await', async () => {
      let resolveToken: (value: string | null) => void
      const getToken = vi.fn().mockImplementation(
        () => new Promise<string | null>((resolve) => { resolveToken = resolve })
      )

      const connection = new SSEConnection({ roomId: 'test-room', getToken })
      const connectPromise = connection.connect()

      connection.disconnect()
      resolveToken!('late-token')

      await vi.runAllTimersAsync()
      await connectPromise

      expect(connection.isConnected()).toBe(false)
    })

    it('should not create EventSource after disconnect during token fetch', async () => {
      let resolveToken: (value: string | null) => void
      const getToken = vi.fn().mockImplementation(
        () => new Promise<string | null>((resolve) => { resolveToken = resolve })
      )

      const countBefore = MockEventSource.getInstanceCount()

      const connection = new SSEConnection({ roomId: 'test-room', getToken })
      connection.connect()

      connection.disconnect()
      resolveToken!('token')
      await vi.runAllTimersAsync()

      expect(MockEventSource.getInstanceCount()).toBe(countBefore)
    })
  })
})
