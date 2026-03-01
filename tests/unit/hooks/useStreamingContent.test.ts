import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import { useStreamingContent } from '@/hooks/useStreamingContent'
import { streamingBuffer } from '@/stores/streaming-buffer'

describe('useStreamingContent', () => {
  beforeEach(() => {
    streamingBuffer.clear()
  })

  afterEach(() => {
    cleanup()
    streamingBuffer.clear()
  })

  it('should return empty result for non-streaming message', () => {
    const { result } = renderHook(() => useStreamingContent('msg-1'))

    expect(result.current.streamingText).toBe('')
    expect(result.current.isStreaming).toBe(false)
  })

  it('should return streaming content when message is streaming', () => {
    streamingBuffer.append('msg-1', 'Hello ')

    const { result } = renderHook(() => useStreamingContent('msg-1'))

    expect(result.current.streamingText).toBe('Hello ')
    expect(result.current.isStreaming).toBe(true)
  })

  it('should update when streaming content changes', () => {
    const { result, rerender } = renderHook(() => useStreamingContent('msg-1'))

    expect(result.current.streamingText).toBe('')

    act(() => {
      streamingBuffer.append('msg-1', 'Hello')
    })

    rerender()

    expect(result.current.streamingText).toBe('Hello')
  })

  it('should return same object reference when content unchanged', () => {
    const { result, rerender } = renderHook(() => useStreamingContent('msg-1'))

    const firstResult = result.current
    rerender()
    const secondResult = result.current

    expect(firstResult).toBe(secondResult)
  })

  it('should return new object when content changes', () => {
    streamingBuffer.append('msg-1', 'Initial')

    const { result, rerender } = renderHook(() => useStreamingContent('msg-1'))

    const firstResult = result.current

    act(() => {
      streamingBuffer.append('msg-1', ' more content')
    })

    rerender()
    const secondResult = result.current

    expect(firstResult).not.toBe(secondResult)
  })

  it('should handle message finalization', () => {
    streamingBuffer.append('msg-1', 'Complete message')

    const { result, rerender } = renderHook(() => useStreamingContent('msg-1'))

    expect(result.current.isStreaming).toBe(true)

    act(() => {
      streamingBuffer.finalize('msg-1')
    })

    rerender()

    expect(result.current.isStreaming).toBe(false)
  })

  it('should handle different message IDs independently', () => {
    streamingBuffer.append('msg-1', 'Message 1')
    streamingBuffer.append('msg-2', 'Message 2')

    const { result: result1 } = renderHook(() => useStreamingContent('msg-1'))
    const { result: result2 } = renderHook(() => useStreamingContent('msg-2'))

    expect(result1.current.streamingText).toBe('Message 1')
    expect(result2.current.streamingText).toBe('Message 2')
  })

  it('should handle clearing streaming buffer', () => {
    streamingBuffer.append('msg-1', 'Content')

    const { result, rerender } = renderHook(() => useStreamingContent('msg-1'))

    expect(result.current.isStreaming).toBe(true)

    act(() => {
      streamingBuffer.clear()
    })

    rerender()

    expect(result.current.isStreaming).toBe(false)
    expect(result.current.streamingText).toBe('')
  })
})
