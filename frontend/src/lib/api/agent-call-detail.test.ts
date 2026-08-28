import { describe, expect, it } from 'vitest'
import {
  canonicalAgentCallParts,
  canonicalArtifactData,
  parseCanonicalCardIdentity,
} from './agent-call-detail'

describe('canonical Agent call detail identity', () => {
  it('preserves declared A2A text and data part types and ordering', () => {
    expect(canonicalAgentCallParts([
      { kind: 'data', data: { client: 'Acme' } },
      { kind: 'text', text: 'Submission prepared.' },
      { kind: 'text', text: '{"declared":"text"}' },
      { kind: 'data', data: [{ quote: 37200 }] },
    ])).toEqual([
      { kind: 'data', data: { client: 'Acme' } },
      { kind: 'text', text: 'Submission prepared.' },
      { kind: 'text', text: '{"declared":"text"}' },
      { kind: 'data', data: [{ quote: 37200 }] },
    ])
    expect(canonicalAgentCallParts(undefined)).toBeUndefined()
  })

  it('maps authenticated room-file descriptors to preview-ready artifacts', () => {
    expect(canonicalArtifactData([{
      artifact_ref: '/api/v1/files/af011190aaba4f97b459e7656bba7f7e/content',
      file_id: 'af011190aaba4f97b459e7656bba7f7e',
      name: 'generated-image.png',
      mime_type: 'image/png',
      size_bytes: 2332106,
    }])).toEqual([{
      artifactId: '/api/v1/files/af011190aaba4f97b459e7656bba7f7e/content',
      name: 'generated-image.png',
      parts: [{
        kind: 'file',
        file: {
          fileId: 'af011190aaba4f97b459e7656bba7f7e',
          name: 'generated-image.png',
          mime_type: 'image/png',
          sizeBytes: 2332106,
        },
      }],
    }])
    expect(canonicalArtifactData([{
      artifact_ref: 'unresolved-private-reference',
    }])).toEqual([])
  })

  it('extracts only the opaque run/call identity and rejects profile-like ids', () => {
    expect(parseCanonicalCardIdentity('orchestrator:run-1:inv_weather_0001')).toEqual({
      runId: 'run-1',
      publicCallId: 'inv_weather_0001',
    })
    expect(parseCanonicalCardIdentity('private-agent-profile-id')).toBeNull()
    expect(parseCanonicalCardIdentity('orchestrator:run-1:private-call-id')).toBeNull()
  })
})
