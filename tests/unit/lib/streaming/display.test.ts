import { describe, expect, it } from 'vitest'
import {
  isBufferStreaming,
  resolveDetailArtifacts,
  resolveEntityStreaming,
  resolveStreamArtifacts,
  resolveStreamBuffer,
  resolveStreamText,
  resolveViewModelStreaming,
} from '@/lib/streaming/display'
import type { StreamBuffer } from '@/stores/streaming-store'

function buffer(overrides: Partial<StreamBuffer> = {}): StreamBuffer {
  return {
    text: 'live',
    artifacts: [],
    isComplete: false,
    roomId: 'room-1',
    lastUpdatedAt: Date.now(),
    ...overrides,
  }
}

describe('streaming display helpers', () => {
  it('isBufferStreaming reflects isComplete', () => {
    expect(isBufferStreaming(undefined)).toBe(false)
    expect(isBufferStreaming(buffer())).toBe(true)
    expect(isBufferStreaming(buffer({ isComplete: true }))).toBe(false)
  })

  it('resolveViewModelStreaming prefers buffer over status', () => {
    expect(resolveViewModelStreaming(undefined, 'working')).toBe(true)
    expect(resolveViewModelStreaming(undefined, 'completed')).toBe(false)
    expect(resolveViewModelStreaming(buffer(), 'completed')).toBe(true)
    expect(resolveViewModelStreaming(buffer({ isComplete: true }), 'working')).toBe(false)
  })

  it('resolveEntityStreaming matches active task states', () => {
    expect(resolveEntityStreaming(undefined, undefined)).toBe(false)
    expect(resolveEntityStreaming(undefined, 'working')).toBe(true)
    expect(resolveEntityStreaming(undefined, 'submitted')).toBe(true)
    expect(resolveEntityStreaming(undefined, 'completed')).toBe(false)
    expect(resolveEntityStreaming(buffer(), 'completed')).toBe(true)
  })

  it('resolveStreamText and artifacts fall back when no buffer', () => {
    expect(resolveStreamText(undefined, 'fallback')).toBe('fallback')
    expect(resolveStreamText(buffer({ text: 'live' }), 'fallback')).toBe('live')
    expect(resolveStreamText(buffer({ text: '' }), 'fallback')).toBe('')
    expect(resolveStreamArtifacts(undefined, [{ artifactId: 'a', parts: [] }])).toHaveLength(1)
    expect(
      resolveStreamArtifacts(buffer({ isComplete: true, artifacts: [] }), [{ artifactId: 'a', parts: [] }]),
    ).toHaveLength(1)
    expect(resolveDetailArtifacts(buffer(), [{ artifactId: 'a', parts: [] }])).toBeUndefined()
    expect(
      resolveDetailArtifacts(buffer({ isComplete: true }), [{ artifactId: 'a', parts: [] }]),
    ).toHaveLength(1)
  })

  it('resolveStreamBuffer returns the message-scoped buffer only', () => {
    const buffers = {
      'msg-1': buffer({ text: 'agent-a live', clientRequestId: 'req-1' }),
      'msg-2': buffer({ text: 'agent-b live', clientRequestId: 'req-1' }),
    }
    expect(resolveStreamBuffer(buffers, 'msg-1')?.text).toBe('agent-a live')
    expect(resolveStreamBuffer(buffers, 'msg-2')?.text).toBe('agent-b live')
    expect(resolveStreamBuffer(buffers, undefined)).toBeUndefined()
  })
})
