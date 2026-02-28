import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'

// We need to mock requestAnimationFrame before importing the module
const rafCallbacks: FrameRequestCallback[] = []
vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
  rafCallbacks.push(callback)
  return rafCallbacks.length
})

// Helper to flush all pending RAF callbacks
function flushRAF() {
  const callbacks = [...rafCallbacks]
  rafCallbacks.length = 0
  callbacks.forEach(cb => cb(performance.now()))
}

// Import after mocking
import { streamingBuffer } from '../streaming-buffer'

describe('StreamingBuffer', () => {
  beforeEach(() => {
    streamingBuffer.clear()
    flushRAF()
    rafCallbacks.length = 0
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  describe('append', () => {
    it('should append tokens to a new buffer', () => {
      streamingBuffer.append('msg-1', 'Hello')
      expect(streamingBuffer.get('msg-1')).toBe('Hello')
    })

    it('should concatenate multiple tokens', () => {
      streamingBuffer.append('msg-1', 'Hello')
      streamingBuffer.append('msg-1', ' ')
      streamingBuffer.append('msg-1', 'World')
      expect(streamingBuffer.get('msg-1')).toBe('Hello World')
    })

    it('should handle multiple message buffers independently', () => {
      streamingBuffer.append('msg-1', 'First')
      streamingBuffer.append('msg-2', 'Second')
      expect(streamingBuffer.get('msg-1')).toBe('First')
      expect(streamingBuffer.get('msg-2')).toBe('Second')
    })
  })

  describe('get', () => {
    it('should return empty string for non-existent buffer', () => {
      expect(streamingBuffer.get('non-existent')).toBe('')
    })

    it('should return accumulated content', () => {
      streamingBuffer.append('msg-1', 'Test')
      expect(streamingBuffer.get('msg-1')).toBe('Test')
    })
  })

  describe('isStreaming', () => {
    it('should return false for non-existent buffer', () => {
      expect(streamingBuffer.isStreaming('non-existent')).toBe(false)
    })

    it('should return true for active buffer', () => {
      streamingBuffer.append('msg-1', 'Test')
      expect(streamingBuffer.isStreaming('msg-1')).toBe(true)
    })

    it('should return false after finalize', () => {
      streamingBuffer.append('msg-1', 'Test')
      streamingBuffer.finalize('msg-1')
      expect(streamingBuffer.isStreaming('msg-1')).toBe(false)
    })
  })

  describe('finalize', () => {
    it('should return accumulated content and remove buffer', () => {
      streamingBuffer.append('msg-1', 'Hello World')
      const content = streamingBuffer.finalize('msg-1')
      expect(content).toBe('Hello World')
      expect(streamingBuffer.get('msg-1')).toBe('')
      expect(streamingBuffer.isStreaming('msg-1')).toBe(false)
    })

    it('should return empty string for non-existent buffer', () => {
      const content = streamingBuffer.finalize('non-existent')
      expect(content).toBe('')
    })
  })

  describe('entries', () => {
    it('should iterate over all active buffers', () => {
      streamingBuffer.append('msg-1', 'First')
      streamingBuffer.append('msg-2', 'Second')
      
      const entries = Array.from(streamingBuffer.entries())
      expect(entries).toHaveLength(2)
      expect(entries).toContainEqual(['msg-1', 'First'])
      expect(entries).toContainEqual(['msg-2', 'Second'])
    })

    it('should return empty iterator when no buffers', () => {
      const entries = Array.from(streamingBuffer.entries())
      expect(entries).toHaveLength(0)
    })
  })

  describe('clear', () => {
    it('should remove all buffers', () => {
      streamingBuffer.append('msg-1', 'First')
      streamingBuffer.append('msg-2', 'Second')
      streamingBuffer.clear()
      
      expect(streamingBuffer.get('msg-1')).toBe('')
      expect(streamingBuffer.get('msg-2')).toBe('')
      expect(streamingBuffer.isStreaming('msg-1')).toBe(false)
      expect(streamingBuffer.isStreaming('msg-2')).toBe(false)
    })
  })

  describe('subscribe and getSnapshot', () => {
    it('should notify listeners on flush', () => {
      const listener = vi.fn()
      const unsubscribe = streamingBuffer.subscribe(listener)
      
      streamingBuffer.append('msg-1', 'Test')
      expect(listener).not.toHaveBeenCalled()
      
      flushRAF()
      expect(listener).toHaveBeenCalledTimes(1)
      
      unsubscribe()
    })

    it('should increment version on flush', () => {
      const initialVersion = streamingBuffer.getSnapshot()
      
      streamingBuffer.append('msg-1', 'Test')
      expect(streamingBuffer.getSnapshot()).toBe(initialVersion)
      
      flushRAF()
      expect(streamingBuffer.getSnapshot()).toBe(initialVersion + 1)
    })

    it('should batch multiple appends into single flush', () => {
      const listener = vi.fn()
      streamingBuffer.subscribe(listener)
      
      streamingBuffer.append('msg-1', 'A')
      streamingBuffer.append('msg-1', 'B')
      streamingBuffer.append('msg-1', 'C')
      
      flushRAF()
      expect(listener).toHaveBeenCalledTimes(1)
    })

    it('should unsubscribe correctly', () => {
      const listener = vi.fn()
      const unsubscribe = streamingBuffer.subscribe(listener)
      
      unsubscribe()
      
      streamingBuffer.append('msg-1', 'Test')
      flushRAF()
      
      expect(listener).not.toHaveBeenCalled()
    })
  })
})
