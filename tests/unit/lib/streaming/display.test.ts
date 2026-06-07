import { describe, expect, it } from 'vitest'
import {
  isBufferStreaming,
  resolveDetailArtifacts,
  resolveEntityStreaming,
  resolveNonTextArtifacts,
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

const fileArtifact = {
  artifactId: 'file-1',
  name: 'report.csv',
  parts: [{ kind: 'file' as const, file: { uri: 's3://bucket/report.csv' } }],
}

const textArtifact = {
  artifactId: 'text-1',
  name: 'response',
  parts: [{ kind: 'text' as const, text: 'live body' }],
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
    expect(resolveStreamText(buffer({ text: '' }), 'fallback')).toBe('fallback')
    expect(resolveStreamText(buffer({ text: 'short' }), 'longer checkpoint')).toBe('short')
    expect(
      resolveStreamText(buffer({ text: 'short', isComplete: true }), 'longer checkpoint'),
    ).toBe('longer checkpoint')
    expect(resolveStreamArtifacts(undefined, [fileArtifact])).toHaveLength(1)
    expect(
      resolveStreamArtifacts(buffer({ isComplete: true, artifacts: [] }), [fileArtifact]),
    ).toHaveLength(1)
  })

  it('resolveNonTextArtifacts merges entity and buffer file artifacts while streaming', () => {
    const liveBuffer = buffer({
      artifacts: [textArtifact, fileArtifact],
    })
    expect(resolveNonTextArtifacts(liveBuffer, [fileArtifact])).toEqual([fileArtifact])
    expect(resolveStreamArtifacts(liveBuffer, [fileArtifact])).toEqual([fileArtifact])
  })

  it('resolveDetailArtifacts keeps non-text entity artifacts visible while streaming', () => {
    expect(resolveDetailArtifacts(buffer(), [fileArtifact])).toEqual([fileArtifact])
    expect(resolveDetailArtifacts(buffer({ isComplete: true }), [fileArtifact])).toEqual([fileArtifact])
    expect(resolveDetailArtifacts(buffer(), [textArtifact])).toBeUndefined()
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
