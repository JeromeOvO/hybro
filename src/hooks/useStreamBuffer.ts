'use client'

import { useMemo } from 'react'
import type { ArtifactData } from '@/stores/message-store/types'
import type { StreamBuffer } from '@/stores/streaming-store'
import { useStreamingStore } from '@/stores/streaming-store'
import {
  resolveStreamArtifacts,
  resolveStreamText,
  resolveViewModelStreaming,
} from '@/lib/streaming/display'
import type { AgentResultViewModel } from '@/lib/room-timeline/types'

/** Subscribe to a single message's stream buffer (avoids re-renders on unrelated chunks). */
export function useStreamBuffer(
  messageId: string | undefined,
  clientRequestId?: string,
): StreamBuffer | undefined {
  return useStreamingStore((s) => {
    if (messageId && s.buffers[messageId]) return s.buffers[messageId]
    if (!clientRequestId) return undefined
    return Object.values(s.buffers).find(buffer => buffer.clientRequestId === clientRequestId)
  })
}

export interface ResultStreamDisplay {
  buffer: StreamBuffer | undefined
  content: string
  artifacts: ArtifactData[] | undefined
  isStreaming: boolean
}

/** Derived stream display fields for AgentResultViewModel-backed UI. */
export function useResultStreamDisplay(
  result: Pick<AgentResultViewModel, 'messageId' | 'clientRequestId' | 'content' | 'artifacts' | 'status'>,
): ResultStreamDisplay {
  const buffer = useStreamBuffer(result.messageId, result.clientRequestId)
  return useMemo(
    () => ({
      buffer,
      content: resolveStreamText(buffer, result.content),
      artifacts: resolveStreamArtifacts(buffer, result.artifacts),
      isStreaming: resolveViewModelStreaming(buffer, result.status),
    }),
    [buffer, result.content, result.artifacts, result.status],
  )
}
