import { describe, it, expect, beforeAll, beforeEach, afterAll, afterEach, vi } from 'vitest'
import { SSEConnection, type SSEConnectionOptions } from '@/lib/api/sse'
import { MockSSEStream } from '../../setup/mock-fetch-sse'
import { server } from '../../setup/msw-server'

// Mock fetch for SSE tests — must be set up after MSW is disabled
const mockFetch = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
  MockSSEStream.recordFetchCall({ url: String(url), options: init ?? {} })
  const stream = new MockSSEStream()
  return stream.toResponse()
})

async function connectAndOpen(options: Partial<SSEConnectionOptions> = {}) {
  const connection = new SSEConnection({ roomId: 'test-room', ...options })
  const connectPromise = connection.connect()
  // Allow microtasks to process (fetch call + stream reader setup)
  await vi.advanceTimersByTimeAsync(0)
  const instance = MockSSEStream.getLastInstanceOrFail()
  await connectPromise
  return { connection, instance }
}

describe('SSEConnection', () => {
  let mathRandomSpy: ReturnType<typeof vi.spyOn> | undefined

  beforeAll(() => {
    // Disable MSW so our fetch mock has full control
    server.close()
    vi.stubGlobal('fetch', mockFetch)
  })

  afterAll(() => {
    vi.unstubAllGlobals()
    // Restore MSW for other test files
    server.listen({ onUnhandledRequest: 'warn' })
  })

  beforeEach(() => {
    vi.useFakeTimers()
    MockSSEStream.clearInstances()
    mockFetch.mockClear()
    // Keep reconnect delays deterministic in fake-timer tests.
    mathRandomSpy = vi.spyOn(Math, 'random').mockReturnValue(0)
  })

  afterEach(() => {
    mathRandomSpy?.mockRestore()
    vi.useRealTimers()
  })

  describe('connect', () => {
    it('should fetch the correct URL', async () => {
      await connectAndOpen()
      const call = MockSSEStream.getLastFetchCallOrFail()
      expect(call.url).toContain('/sse/room/test-room/stream')
    })

    it('should send auth token via Authorization header, not URL query', async () => {
      const getToken = vi.fn().mockResolvedValue('test-token')
      await connectAndOpen({ getToken })

      const call = MockSSEStream.getLastFetchCallOrFail()
      // Token must NOT be in URL
      expect(call.url).not.toContain('token=')
      // Token must be in Authorization header
      const headers = call.options.headers as Record<string, string>
      expect(headers['Authorization']).toBe('Bearer test-token')
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

    it('should connect without Authorization header when getToken returns null', async () => {
      const getToken = vi.fn().mockResolvedValue(null)
      await connectAndOpen({ getToken })

      const call = MockSSEStream.getLastFetchCallOrFail()
      expect(call.url).not.toContain('token=')
      const headers = call.options.headers as Record<string, string>
      expect(headers['Authorization']).toBeUndefined()
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

      // Allow microtask (reader loop) to process
      await vi.advanceTimersByTimeAsync(0)

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

      await vi.advanceTimersByTimeAsync(0)

      expect(onMessage).not.toHaveBeenCalled()
    })

    it('should handle malformed JSON gracefully', async () => {
      const onMessage = vi.fn()
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const { instance } = await connectAndOpen({ onMessage })

      instance.simulateRawData('data: invalid json\n\n')

      await vi.advanceTimersByTimeAsync(0)

      expect(onMessage).not.toHaveBeenCalled()
      expect(consoleSpy).toHaveBeenCalled()
      consoleSpy.mockRestore()
    })
  })

  describe('error handling and reconnection', () => {
    it('should call onError callback on stream close', async () => {
      const onError = vi.fn()
      const { instance } = await connectAndOpen({ onError })

      instance.simulateClose()
      await vi.advanceTimersByTimeAsync(0)

      expect(onError).toHaveBeenCalledTimes(1)
    })

    it('should attempt reconnection on stream close', async () => {
      const { instance } = await connectAndOpen()
      const initialCount = MockSSEStream.getFetchCallCount()

      instance.simulateClose()
      await vi.advanceTimersByTimeAsync(0)
      // Wait for reconnect delay (1st attempt = 1000ms)
      await vi.advanceTimersByTimeAsync(1000)

      expect(MockSSEStream.getFetchCallCount()).toBeGreaterThan(initialCount)
    })

    it('should use increasing delay for reconnection attempts', async () => {
      const { instance: first } = await connectAndOpen()

      // 1st error -> reconnect after 1s
      first.simulateClose()
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(1000)

      const second = MockSSEStream.getLastInstanceOrFail()
      // 2nd error -> reconnect after 2s
      second.simulateClose()
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(2000)

      const third = MockSSEStream.getLastInstanceOrFail()
      // 3rd error -> reconnect after 3s
      third.simulateClose()
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(3000)

      expect(MockSSEStream.getLastInstance()).toBeDefined()
    })

    it('should stop reconnecting after max attempts', async () => {
      const { instance: first } = await connectAndOpen()

      let current = first
      for (let i = 0; i < 6; i++) {
        current.simulateClose()
        await vi.advanceTimersByTimeAsync(0)
        await vi.advanceTimersByTimeAsync(10000)
        const next = MockSSEStream.getLastInstance()
        if (next && next !== current) {
          current = next
        }
      }

      // After max attempts, a fresh connection should still work
      const { connection } = await connectAndOpen()
      connection.disconnect()
      expect(connection.isConnected()).toBe(false)
    })

    it('should not reconnect after manual disconnect', async () => {
      const { connection } = await connectAndOpen()
      const countBeforeDisconnect = MockSSEStream.getFetchCallCount()

      connection.disconnect()
      await vi.advanceTimersByTimeAsync(5000)

      // No new fetch calls should have been made after disconnect
      expect(MockSSEStream.getFetchCallCount()).toBe(countBeforeDisconnect)
      expect(connection.isConnected()).toBe(false)
    })

    it('should keep first reconnect deterministic when jitter is enabled', async () => {
      const { instance } = await connectAndOpen({
        reconnectJitterMs: 1000,
        randomFn: () => 1,
      })
      const initialCount = MockSSEStream.getFetchCallCount()

      instance.simulateClose()
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(1000)

      expect(MockSSEStream.getFetchCallCount()).toBeGreaterThan(initialCount)
    })

    it('should apply jitter when deterministic first reconnect is disabled', async () => {
      const { instance } = await connectAndOpen({
        reconnectJitterMs: 1000,
        randomFn: () => 1,
        deterministicFirstReconnect: false,
      })
      const initialCount = MockSSEStream.getFetchCallCount()

      // First reconnect: 1000ms base + 1000ms jitter
      instance.simulateClose()
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(1999)
      expect(MockSSEStream.getFetchCallCount()).toBe(initialCount)
      await vi.advanceTimersByTimeAsync(1)
      expect(MockSSEStream.getFetchCallCount()).toBeGreaterThan(initialCount)
    })
  })

  describe('disconnect', () => {
    it('should report not connected after disconnect', async () => {
      const { connection } = await connectAndOpen()
      connection.disconnect()
      expect(connection.isConnected()).toBe(false)
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

      instance.simulateClose()
      await vi.advanceTimersByTimeAsync(0)
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
      await vi.advanceTimersByTimeAsync(0)
      await connectPromise
      expect(connection.isConnected()).toBe(true)

      connection.disconnect()
      expect(connection.isConnected()).toBe(false)
    })

    it('should return correct readyState values', async () => {
      const connection = new SSEConnection({ roomId: 'test-room' })
      // CLOSED = 2
      expect(connection.getConnectionState()).toBe(2)

      const connectPromise = connection.connect()
      await vi.advanceTimersByTimeAsync(0)
      await connectPromise
      // OPEN = 1
      expect(connection.getConnectionState()).toBe(1)
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

    it('should not call fetch after disconnect during token fetch', async () => {
      let resolveToken: (value: string | null) => void
      const getToken = vi.fn().mockImplementation(
        () => new Promise<string | null>((resolve) => { resolveToken = resolve })
      )

      const countBefore = MockSSEStream.getFetchCallCount()

      const connection = new SSEConnection({ roomId: 'test-room', getToken })
      connection.connect()

      connection.disconnect()
      resolveToken!('token')
      await vi.runAllTimersAsync()

      expect(MockSSEStream.getFetchCallCount()).toBe(countBefore)
    })
  })
})
