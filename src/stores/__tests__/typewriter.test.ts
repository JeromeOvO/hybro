import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'

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

import { streamingBuffer } from '../streaming-buffer'
import { startTypewriter, TypewriterManager } from '../typewriter'

describe('startTypewriter', () => {
  beforeEach(() => {
    streamingBuffer.clear()
    flushRAF()
    rafCallbacks.length = 0
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('should progressively feed text into the streaming buffer', () => {
    const onComplete = vi.fn()
    startTypewriter('msg-1', 'Hello World!', onComplete)

    expect(streamingBuffer.isStreaming('msg-1')).toBe(true)

    vi.advanceTimersByTime(16)
    const partialContent = streamingBuffer.get('msg-1')
    expect(partialContent.length).toBeGreaterThan(0)
    expect(partialContent.length).toBeLessThanOrEqual('Hello World!'.length)
  })

  it('should call onComplete when all text has been fed', () => {
    const onComplete = vi.fn()
    startTypewriter('msg-1', 'Short', onComplete)

    vi.advanceTimersByTime(2000)

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(streamingBuffer.get('msg-1')).toBe('Short')
  })

  it('should handle empty text by completing immediately', () => {
    const onComplete = vi.fn()
    startTypewriter('msg-1', '', onComplete)

    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it('finish() should jump to end and call onComplete', () => {
    const onComplete = vi.fn()
    const handle = startTypewriter('msg-1', 'Hello World this is a longer text', onComplete)

    vi.advanceTimersByTime(16)
    expect(streamingBuffer.get('msg-1').length).toBeLessThan(33)

    handle.finish()

    expect(streamingBuffer.get('msg-1')).toBe('Hello World this is a longer text')
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it('finish() should be idempotent', () => {
    const onComplete = vi.fn()
    const handle = startTypewriter('msg-1', 'Test', onComplete)

    handle.finish()
    handle.finish()
    handle.finish()

    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it('should not call onComplete twice when timer finishes after finish()', () => {
    const onComplete = vi.fn()
    const handle = startTypewriter('msg-1', 'Test', onComplete)

    handle.finish()
    vi.advanceTimersByTime(5000)

    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it('should deliver complete text when timer runs to completion', () => {
    const longText = 'A'.repeat(500)
    const onComplete = vi.fn()
    startTypewriter('msg-1', longText, onComplete)

    vi.advanceTimersByTime(5000)

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(streamingBuffer.get('msg-1')).toBe(longText)
  })

  it('abort() should stop without calling onComplete', () => {
    const onComplete = vi.fn()
    const handle = startTypewriter('msg-1', 'Hello World this is text', onComplete)

    vi.advanceTimersByTime(16)
    handle.abort()

    expect(onComplete).not.toHaveBeenCalled()
    // Buffer entry should be cleared by abort
    expect(streamingBuffer.isStreaming('msg-1')).toBe(false)
  })

  it('abort() should be idempotent', () => {
    const onComplete = vi.fn()
    const handle = startTypewriter('msg-1', 'Test', onComplete)

    handle.abort()
    handle.abort()

    expect(onComplete).not.toHaveBeenCalled()
  })

  it('abort() prevents onComplete even when timer would have completed', () => {
    const onComplete = vi.fn()
    const handle = startTypewriter('msg-1', 'Test', onComplete)

    handle.abort()
    vi.advanceTimersByTime(5000)

    expect(onComplete).not.toHaveBeenCalled()
  })

  it('finish() after abort() is a no-op', () => {
    const onComplete = vi.fn()
    const handle = startTypewriter('msg-1', 'Test', onComplete)

    handle.abort()
    handle.finish()

    expect(onComplete).not.toHaveBeenCalled()
  })
})

describe('TypewriterManager', () => {
  let manager: TypewriterManager

  beforeEach(() => {
    manager = new TypewriterManager()
    streamingBuffer.clear()
    flushRAF()
    rafCallbacks.length = 0
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('should track active typewriters', () => {
    const onComplete = vi.fn()
    manager.start('msg-1', 'Hello', onComplete)

    expect(manager.isActive('msg-1')).toBe(true)
    expect(manager.isActive('msg-2')).toBe(false)
  })

  it('should remove typewriter from active set on completion', () => {
    const onComplete = vi.fn()
    manager.start('msg-1', 'Hi', onComplete)

    vi.advanceTimersByTime(5000)

    expect(manager.isActive('msg-1')).toBe(false)
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it('finish() should jump to end and call onComplete', () => {
    const onComplete = vi.fn()
    manager.start('msg-1', 'Hello World', onComplete)

    manager.finish('msg-1')

    expect(manager.isActive('msg-1')).toBe(false)
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(streamingBuffer.get('msg-1')).toBe('Hello World')
  })

  it('finish() should be safe for non-existent message', () => {
    expect(() => manager.finish('non-existent')).not.toThrow()
  })

  it('abort() should stop without calling onComplete', () => {
    const onComplete = vi.fn()
    manager.start('msg-1', 'Hello World', onComplete)

    manager.abort('msg-1')

    expect(manager.isActive('msg-1')).toBe(false)
    expect(onComplete).not.toHaveBeenCalled()
    expect(streamingBuffer.isStreaming('msg-1')).toBe(false)
  })

  it('abort() should be safe for non-existent message', () => {
    expect(() => manager.abort('non-existent')).not.toThrow()
  })

  it('start() should finish previous typewriter for same message', () => {
    const onComplete1 = vi.fn()
    const onComplete2 = vi.fn()

    manager.start('msg-1', 'First content', onComplete1)
    manager.start('msg-1', 'Second content', onComplete2)

    expect(onComplete1).toHaveBeenCalledTimes(1)

    expect(manager.isActive('msg-1')).toBe(true)

    vi.advanceTimersByTime(5000)
    expect(onComplete2).toHaveBeenCalledTimes(1)
  })

  it('finishAll() should finish all active typewriters', () => {
    const onComplete1 = vi.fn()
    const onComplete2 = vi.fn()

    manager.start('msg-1', 'First', onComplete1)
    manager.start('msg-2', 'Second', onComplete2)

    manager.finishAll()

    expect(manager.isActive('msg-1')).toBe(false)
    expect(manager.isActive('msg-2')).toBe(false)
    expect(onComplete1).toHaveBeenCalledTimes(1)
    expect(onComplete2).toHaveBeenCalledTimes(1)
  })

  it('should handle concurrent typewriters for different messages', () => {
    const onComplete1 = vi.fn()
    const onComplete2 = vi.fn()

    manager.start('msg-1', 'Hello', onComplete1)
    manager.start('msg-2', 'World', onComplete2)

    expect(manager.isActive('msg-1')).toBe(true)
    expect(manager.isActive('msg-2')).toBe(true)

    vi.advanceTimersByTime(5000)

    expect(onComplete1).toHaveBeenCalledTimes(1)
    expect(onComplete2).toHaveBeenCalledTimes(1)
    expect(streamingBuffer.get('msg-1')).toBe('Hello')
    expect(streamingBuffer.get('msg-2')).toBe('World')
  })
})
