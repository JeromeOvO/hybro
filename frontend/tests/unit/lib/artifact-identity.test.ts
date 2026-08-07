import { describe, expect, it } from 'vitest'
import {
  countDurableArtifactFiles,
  deduplicateArtifactsByPart,
  hasUnavailableArtifactOutput,
  hasUsableArtifactOutput,
} from '@/lib/artifacts/artifact-identity'

describe('artifact identity and availability', () => {
  it('counts only file parts the renderer can open', () => {
    const artifacts = [{
      artifactId: 'artifact-1',
      parts: [
        { kind: 'file' as const, file: { fileId: 'file-1' } },
        { kind: 'file' as const, file: { uri: '/files/2' } },
        { kind: 'data' as const, data: { type: 'file_unavailable' } },
      ],
    }]

    expect(countDurableArtifactFiles(artifacts)).toBe(1)
    expect(hasUnavailableArtifactOutput(artifacts)).toBe(true)
  })

  it('deduplicates canonical data regardless of object key order', () => {
    const artifacts = deduplicateArtifactsByPart([
      {
        artifactId: 'real',
        parts: [{ kind: 'data', data: { type: 'file_unavailable', reason: 'x' } }],
      },
      {
        artifactId: 'synthetic',
        isSynthetic: true,
        parts: [{ kind: 'data', data: { reason: 'x', type: 'file_unavailable' } }],
      },
    ])

    expect(artifacts).toHaveLength(1)
    expect(artifacts?.[0].artifactId).toBe('real')
  })

  it('keeps the canonical owner when synthetic data arrives first', () => {
    const artifacts = deduplicateArtifactsByPart([
      {
        artifactId: 'synthetic',
        isSynthetic: true,
        parts: [
          { kind: 'file', file: { fileId: 'shared' } },
          { kind: 'file', file: { fileId: 'synthetic-only' } },
        ],
      },
      {
        artifactId: 'canonical',
        parts: [{ kind: 'file', file: { fileId: 'shared' } }],
      },
    ])

    expect(artifacts).toEqual([
      {
        artifactId: 'synthetic',
        isSynthetic: true,
        parts: [{ kind: 'file', file: { fileId: 'synthetic-only' } }],
      },
      {
        artifactId: 'canonical',
        parts: [{ kind: 'file', file: { fileId: 'shared' } }],
      },
    ])
  })

  it('deduplicates repeated parts within one artifact', () => {
    const artifacts = deduplicateArtifactsByPart([{
      artifactId: 'canonical',
      parts: [
        { kind: 'file', file: { fileId: 'file-1' } },
        { kind: 'file', file: { fileId: 'file-1' } },
      ],
    }])

    expect(artifacts?.[0].parts).toHaveLength(1)
  })

  it('does not treat empty or unavailable data as useful output', () => {
    expect(hasUsableArtifactOutput([{
      artifactId: 'empty',
      parts: [
        { kind: 'data', data: {} },
        { kind: 'data', data: { type: 'file_unavailable' } },
      ],
    }])).toBe(false)
    expect(hasUsableArtifactOutput([{
      artifactId: 'useful',
      parts: [{ kind: 'data', data: { result: 1 } }],
    }])).toBe(true)
  })
})
