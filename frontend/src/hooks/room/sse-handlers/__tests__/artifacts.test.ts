import { describe, expect, it } from 'vitest'
import type { MessageEntity } from '@/stores/message-store/types'
import {
  partsToArtifacts,
  partsToReplacementArtifacts,
  sseArtifactDataFromPayload,
} from '../artifacts'

describe('SSE artifact conversion privacy', () => {
  it('drops legacy task_update file bytes when no URI is available', () => {
    const privateBytes = 'PRIVATE_SENTINEL_sse_task_update_bytes'

    const artifacts = partsToArtifacts(
      [{
        kind: 'file',
        file: {
          bytes: privateBytes,
          mime_type: 'text/plain',
          name: 'private.txt',
        },
      }],
      'message-1',
      undefined,
    )

    expect(artifacts).toBeUndefined()
  })

  it('keeps URI-backed task_update files but strips legacy bytes', () => {
    const privateBytes = 'PRIVATE_SENTINEL_sse_task_update_bytes'

    const artifacts = partsToReplacementArtifacts(
      [{
        kind: 'file',
        file: {
          uri: 'https://storage.example/result.txt',
          bytes: privateBytes,
          mimeType: 'text/plain',
          name: 'result.txt',
        },
      }],
      'message-1',
    )

    expect(JSON.stringify(artifacts)).not.toContain(privateBytes)
    expect(artifacts).toEqual([{
      artifactId: 'message-1-parts',
      name: 'Response files',
      isSynthetic: true,
      parts: [{
        kind: 'file',
        text: undefined,
        file: {
          uri: 'https://storage.example/result.txt',
          mime_type: 'text/plain',
          name: 'result.txt',
        },
        data: undefined,
      }],
    }])
  })

  it('drops legacy artifact_update file bytes when no URI is available', () => {
    const privateBytes = 'PRIVATE_SENTINEL_sse_artifact_update_bytes'

    const artifact = sseArtifactDataFromPayload(
      {
        artifact_id: 'artifact-1',
        name: 'partial',
        parts: [{
          kind: 'file',
          file: {
            bytes: privateBytes,
            mime_type: 'text/plain',
            name: 'private.txt',
          },
        }],
      },
      true,
      false,
    )

    expect(JSON.stringify(artifact)).not.toContain(privateBytes)
    expect(artifact.parts).toEqual([])
  })

  it('does not add synthetic task parts already present under a real artifact id', () => {
    const existing = {
      artifacts: [{
        artifactId: 'artifact-real',
        parts: [{ kind: 'file', file: { fileId: 'file-123', sha256: 'digest' } }],
      }],
    } as MessageEntity

    const artifacts = partsToArtifacts(
      [{
        kind: 'file',
        metadata: {
          file_id: 'file-123',
          file_name: 'image.png',
          sha256: 'digest',
        },
      }],
      'message-1',
      existing,
    )

    expect(artifacts).toEqual(existing.artifacts)
  })

  it('does not replace a canonical artifact on synthetic id collision', () => {
    const existing = {
      artifacts: [{
        artifactId: 'message-1-parts',
        parts: [{ kind: 'file', file: { fileId: 'canonical-file' } }],
      }],
    } as MessageEntity

    const artifacts = partsToArtifacts(
      [{
        kind: 'file',
        metadata: { file_id: 'new-file' },
      }],
      'message-1',
      existing,
    )

    expect(artifacts?.map(artifact => artifact.artifactId)).toEqual([
      'message-1-parts',
      'message-1-parts:synthetic',
    ])
    expect(artifacts?.[0].parts[0].file?.fileId).toBe('canonical-file')
  })

  it('allocates an unused synthetic id when both standard ids are canonical', () => {
    const existing = {
      artifacts: [
        {
          artifactId: 'message-1-parts',
          parts: [{ kind: 'file', file: { fileId: 'canonical-1' } }],
        },
        {
          artifactId: 'message-1-parts:synthetic',
          parts: [{ kind: 'file', file: { fileId: 'canonical-2' } }],
        },
      ],
    } as MessageEntity

    const artifacts = partsToArtifacts(
      [{ kind: 'file', metadata: { file_id: 'new-file' } }],
      'message-1',
      existing,
    )

    expect(artifacts?.map(artifact => artifact.artifactId)).toEqual([
      'message-1-parts',
      'message-1-parts:synthetic',
      'message-1-parts:synthetic-2',
    ])
    expect(artifacts?.[0].parts[0].file?.fileId).toBe('canonical-1')
    expect(artifacts?.[1].parts[0].file?.fileId).toBe('canonical-2')
  })

  it('replacement parts preserve canonical artifacts and remove batch duplicates', () => {
    const existing = [{
      artifactId: 'canonical',
      parts: [{ kind: 'file' as const, file: { fileId: 'file-1' } }],
    }]

    const artifacts = partsToReplacementArtifacts(
      [
        { kind: 'file', metadata: { file_id: 'file-1' } },
        { kind: 'file', metadata: { file_id: 'file-2' } },
        { kind: 'file', metadata: { file_id: 'file-2' } },
      ],
      'message-1',
      existing,
    )

    expect(artifacts).toHaveLength(2)
    expect(artifacts[0]).toEqual(existing[0])
    expect(artifacts[1].parts).toHaveLength(1)
    expect(artifacts[1].parts[0].file?.fileId).toBe('file-2')
  })

  it('replacement parts avoid all canonical synthetic-id candidates', () => {
    const existing = [
      {
        artifactId: 'message-1-parts',
        parts: [{ kind: 'file' as const, file: { fileId: 'canonical-1' } }],
      },
      {
        artifactId: 'message-1-parts:synthetic',
        parts: [{ kind: 'file' as const, file: { fileId: 'canonical-2' } }],
      },
    ]

    const artifacts = partsToReplacementArtifacts(
      [{ kind: 'file', metadata: { file_id: 'new-file' } }],
      'message-1',
      existing,
    )

    expect(artifacts.map(artifact => artifact.artifactId)).toEqual([
      'message-1-parts',
      'message-1-parts:synthetic',
      'message-1-parts:synthetic-2',
    ])
  })

  it('keeps durable metadata-only files in terminal SSE artifacts', () => {
    const artifact = sseArtifactDataFromPayload(
      {
        artifact_id: 'artifact-1',
        name: 'result',
        parts: [{
          kind: 'file',
          metadata: {
            file_id: 'a'.repeat(32),
            file_name: 'result.csv',
            mime_type: 'text/csv',
            size_bytes: 42,
            sha256: 'digest',
          },
        }],
      },
      false,
      true,
    )

    expect(artifact.parts).toEqual([{
      kind: 'file',
      text: undefined,
      file: {
        uri: undefined,
        fileId: 'a'.repeat(32),
        mime_type: 'text/csv',
        name: 'result.csv',
        sizeBytes: 42,
        sha256: 'digest',
      },
      data: undefined,
    }])
  })
})
