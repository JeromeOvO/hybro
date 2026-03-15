import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { streamingBuffer } from '@/stores/streaming-buffer'

const rafCallbacks: FrameRequestCallback[] = []
vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
  rafCallbacks.push(callback)
  return rafCallbacks.length
})

function flushRAF() {
  const callbacks = [...rafCallbacks]
  rafCallbacks.length = 0
  callbacks.forEach(cb => cb(performance.now()))
}

describe('StreamingBuffer', () => {
  beforeEach(() => {
    streamingBuffer.clear()
    flushRAF()
    rafCallbacks.length = 0
  })

  afterEach(() => {
    streamingBuffer.clear()
  })

  describe('append', () => {
    it('should buffer partial lines until a newline arrives', () => {
      streamingBuffer.append('msg-1', 'Hello')
      streamingBuffer.append('msg-1', ' World')

      expect(streamingBuffer.get('msg-1')).toBe('')
    })

    it('should expose committed lines for new messages independently', () => {
      streamingBuffer.append('msg-1', 'First\n')
      streamingBuffer.append('msg-2', 'Second\n')

      expect(streamingBuffer.get('msg-1')).toBe('First')
      expect(streamingBuffer.get('msg-2')).toBe('Second')
    })

    it('should handle empty tokens', () => {
      streamingBuffer.append('msg-1', '')
      streamingBuffer.append('msg-1', 'Content\n')
      streamingBuffer.append('msg-1', '')

      expect(streamingBuffer.get('msg-1')).toBe('Content')
    })

    it('should preserve committed multiline content for later markdown rendering', () => {
      streamingBuffer.append('msg-1', '```python\n')
      streamingBuffer.append('msg-1', 'print("Hello")\n')
      streamingBuffer.append('msg-1', '```')

      expect(streamingBuffer.get('msg-1')).toBe('```python\nprint("Hello")')
    })

    it('should preserve single-newline token streams until the preview layer normalizes them', () => {
      streamingBuffer.append('msg-1', 'word1\nword2\nword3\n')

      expect(streamingBuffer.get('msg-1')).toBe('word1\nword2\nword3')
    })

    it('should preserve blank lines in committed content', () => {
      streamingBuffer.append('msg-1', 'word1\n \nword2\r\n\r\nword3\n')

      expect(streamingBuffer.get('msg-1')).toBe('word1\n \nword2\n\nword3')
    })
  })

  describe('get', () => {
    it('should return empty string for non-existent message', () => {
      expect(streamingBuffer.get('non-existent')).toBe('')
    })

    it('should return only committed render-safe content', () => {
      streamingBuffer.append('msg-1', 'A')
      streamingBuffer.append('msg-1', 'B')
      streamingBuffer.append('msg-1', 'C\n')

      expect(streamingBuffer.get('msg-1')).toBe('ABC')
    })
  })

  describe('isStreaming', () => {
    it('should return false for non-existent message', () => {
      expect(streamingBuffer.isStreaming('msg-1')).toBe(false)
    })

    it('should return true after append', () => {
      streamingBuffer.append('msg-1', 'Content')

      expect(streamingBuffer.isStreaming('msg-1')).toBe(true)
    })

    it('should return false after finalize', () => {
      streamingBuffer.append('msg-1', 'Content')
      streamingBuffer.finalize('msg-1')

      expect(streamingBuffer.isStreaming('msg-1')).toBe(false)
    })
  })

  describe('finalize', () => {
    it('should return accumulated content', () => {
      streamingBuffer.append('msg-1', 'Hello ')
      streamingBuffer.append('msg-1', 'World')

      const content = streamingBuffer.finalize('msg-1')

      expect(content).toBe('Hello World')
    })

    it('should remove buffer entry', () => {
      streamingBuffer.append('msg-1', 'Content')
      streamingBuffer.finalize('msg-1')

      expect(streamingBuffer.isStreaming('msg-1')).toBe(false)
      expect(streamingBuffer.get('msg-1')).toBe('')
    })

    it('should return empty string for non-existent message', () => {
      const content = streamingBuffer.finalize('non-existent')

      expect(content).toBe('')
    })
  })

  describe('clear', () => {
    it('should remove all buffers', () => {
      streamingBuffer.append('msg-1', 'Content 1')
      streamingBuffer.append('msg-2', 'Content 2')
      streamingBuffer.append('msg-3', 'Content 3')

      streamingBuffer.clear()

      expect(streamingBuffer.isStreaming('msg-1')).toBe(false)
      expect(streamingBuffer.isStreaming('msg-2')).toBe(false)
      expect(streamingBuffer.isStreaming('msg-3')).toBe(false)
    })
  })

  describe('entries', () => {
    it('should iterate over all active raw buffers', () => {
      streamingBuffer.append('msg-1', 'Content 1')
      streamingBuffer.append('msg-2', 'Content 2')

      const entries = Array.from(streamingBuffer.entries())

      expect(entries).toHaveLength(2)
      expect(entries).toContainEqual(['msg-1', 'Content 1'])
      expect(entries).toContainEqual(['msg-2', 'Content 2'])
    })

    it('should return empty iterator when no buffers', () => {
      const entries = Array.from(streamingBuffer.entries())

      expect(entries).toHaveLength(0)
    })
  })

  describe('subscription', () => {
    it('should notify listeners on append', () => {
      const listener = vi.fn()
      const unsubscribe = streamingBuffer.subscribe(listener)

      streamingBuffer.append('msg-1', 'Content')
      flushRAF()

      expect(listener).toHaveBeenCalled()

      unsubscribe()
    })

    it('should notify listeners on finalize', () => {
      const listener = vi.fn()
      streamingBuffer.append('msg-1', 'Content')
      flushRAF()

      const unsubscribe = streamingBuffer.subscribe(listener)

      streamingBuffer.finalize('msg-1')
      flushRAF()

      expect(listener).toHaveBeenCalled()

      unsubscribe()
    })

    it('should notify listeners on clear', () => {
      const listener = vi.fn()
      streamingBuffer.append('msg-1', 'Content')
      flushRAF()

      const unsubscribe = streamingBuffer.subscribe(listener)

      streamingBuffer.clear()
      flushRAF()

      expect(listener).toHaveBeenCalled()

      unsubscribe()
    })

    it('should not notify after unsubscribe', () => {
      const listener = vi.fn()
      const unsubscribe = streamingBuffer.subscribe(listener)

      unsubscribe()

      streamingBuffer.append('msg-1', 'Content')
      flushRAF()

      expect(listener).not.toHaveBeenCalled()
    })

    it('should batch multiple appends into single notification', () => {
      const listener = vi.fn()
      const unsubscribe = streamingBuffer.subscribe(listener)

      streamingBuffer.append('msg-1', 'A')
      streamingBuffer.append('msg-1', 'B')
      streamingBuffer.append('msg-1', 'C')

      expect(listener).not.toHaveBeenCalled()

      flushRAF()

      expect(listener).toHaveBeenCalledTimes(1)

      unsubscribe()
    })
  })

  describe('snapshots', () => {
    it('should increment global snapshot on changes', () => {
      const v1 = streamingBuffer.getSnapshot()

      streamingBuffer.append('msg-1', 'Content')
      flushRAF()

      const v2 = streamingBuffer.getSnapshot()

      expect(v2).toBeGreaterThan(v1)
    })

    it('should increment per-message snapshot on append', () => {
      const v1 = streamingBuffer.getMessageSnapshot('msg-1')

      streamingBuffer.append('msg-1', 'Content')

      const v2 = streamingBuffer.getMessageSnapshot('msg-1')

      expect(v2).toBeGreaterThan(v1)
    })

    it('should increment per-message snapshot on finalize', () => {
      streamingBuffer.append('msg-1', 'Content')
      const v1 = streamingBuffer.getMessageSnapshot('msg-1')

      streamingBuffer.finalize('msg-1')

      const v2 = streamingBuffer.getMessageSnapshot('msg-1')

      expect(v2).toBeGreaterThan(v1)
    })

    it('should return 0 for non-existent message snapshot', () => {
      expect(streamingBuffer.getMessageSnapshot('non-existent')).toBe(0)
    })

    it('should not affect other message snapshots', () => {
      streamingBuffer.append('msg-1', 'Content 1')
      const v1 = streamingBuffer.getMessageSnapshot('msg-2')

      streamingBuffer.append('msg-1', 'More content')

      const v2 = streamingBuffer.getMessageSnapshot('msg-2')

      expect(v2).toBe(v1)
    })
  })

  describe('performance', () => {
    it('should handle rapid token appends correctly', () => {
      for (let i = 0; i < 1000; i++) {
        streamingBuffer.append('msg-1', 'token ')
      }

      expect(streamingBuffer.get('msg-1')).toBe('')
    })

    it('should handle multiple concurrent streams', () => {
      for (let i = 0; i < 10; i++) {
        for (let j = 0; j < 100; j++) {
          streamingBuffer.append(`msg-${i}`, `token-${j}\n`)
        }
      }

      for (let i = 0; i < 10; i++) {
        expect(streamingBuffer.isStreaming(`msg-${i}`)).toBe(true)
        expect(streamingBuffer.get(`msg-${i}`).length).toBeGreaterThan(0)
      }
    })
  })
})
