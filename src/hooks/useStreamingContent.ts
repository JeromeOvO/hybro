'use client'

import { useRef, useCallback, useSyncExternalStore } from 'react'
import { streamingBuffer } from '@/stores/streaming-buffer'

interface StreamingContentResult {
  streamingText: string
  isStreaming: boolean
}

const EMPTY_RESULT: StreamingContentResult = { streamingText: '', isStreaming: false }

/**
 * Hook to access streaming content for a specific message.
 * 
 * Uses useSyncExternalStore with a per-message snapshot so only the
 * component for the actively streaming message re-renders. Non-streaming
 * bubbles see the same snapshot value across updates and bail out.
 */
export function useStreamingContent(messageId: string): StreamingContentResult {
  const prevRef = useRef<StreamingContentResult>(EMPTY_RESULT)

  const getSnapshot = useCallback(
    () => streamingBuffer.getMessageSnapshot(messageId),
    [messageId],
  )

  useSyncExternalStore(
    streamingBuffer.subscribe.bind(streamingBuffer),
    getSnapshot,
    () => 0 // Server snapshot for SSR
  )

  const streamingText = streamingBuffer.get(messageId)
  const isStreaming = streamingBuffer.isStreaming(messageId)

  // Return the same object reference if nothing changed for this message.
  const prev = prevRef.current
  if (prev.streamingText === streamingText && prev.isStreaming === isStreaming) {
    return prev
  }

  const next: StreamingContentResult = { streamingText, isStreaming }
  prevRef.current = next
  return next
}
