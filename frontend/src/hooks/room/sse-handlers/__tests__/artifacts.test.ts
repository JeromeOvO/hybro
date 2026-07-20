import { describe, expect, it } from 'vitest'
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
})
