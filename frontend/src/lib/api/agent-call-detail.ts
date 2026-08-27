import { apiClient } from '@/lib/api-client'
import { getApiUrl } from '@/lib/utils'
import type { ArtifactData, ArtifactPart } from '@/stores/message-store/types'

export interface CanonicalArtifactDescriptor {
  artifact_ref: string
  file_id?: string | null
  name?: string | null
  mime_type?: string | null
  size_bytes?: number | null
}

export type CanonicalAgentCallPart =
  | { kind: 'text'; text: string }
  | { kind: 'data'; data: Record<string, unknown> | unknown[] }

export interface CanonicalAgentCallDetailResponse {
  run_id: string
  public_call_id: string
  status: string
  /** Compatibility projection for frontend instances predating typed parts. */
  output: string
  /** Absent only while talking to an older backend during a rolling deploy. */
  parts?: CanonicalAgentCallPart[]
  artifacts: CanonicalArtifactDescriptor[]
}

export function canonicalAgentCallParts(
  parts: CanonicalAgentCallPart[] | undefined,
): ArtifactPart[] | undefined {
  if (parts === undefined) return undefined
  const normalized: ArtifactPart[] = []
  for (const part of parts) {
    if (part.kind === 'text' && typeof part.text === 'string') {
      normalized.push({ kind: 'text', text: part.text })
    } else if (
      part.kind === 'data'
      && part.data !== null
      && typeof part.data === 'object'
    ) {
      normalized.push({ kind: 'data', data: part.data })
    }
  }
  return normalized
}

export function canonicalArtifactData(
  artifacts: CanonicalArtifactDescriptor[],
): ArtifactData[] {
  return artifacts.flatMap((artifact) => {
    if (!artifact.file_id) return []
    return [{
      artifactId: artifact.artifact_ref,
      name: artifact.name ?? undefined,
      parts: [{
        kind: 'file' as const,
        file: {
          fileId: artifact.file_id,
          name: artifact.name ?? undefined,
          mime_type: artifact.mime_type ?? undefined,
          sizeBytes: artifact.size_bytes ?? undefined,
        },
      }],
    }]
  })
}

export function parseCanonicalCardIdentity(messageId: string): {
  runId: string
  publicCallId: string
} | null {
  const prefix = 'orchestrator:'
  if (!messageId.startsWith(prefix)) return null
  const publicSeparator = messageId.lastIndexOf(':inv_')
  if (publicSeparator <= prefix.length) return null
  return {
    runId: messageId.slice(prefix.length, publicSeparator),
    publicCallId: messageId.slice(publicSeparator + 1),
  }
}

export async function fetchCanonicalAgentCallDetail(
  roomId: string,
  messageId: string,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal,
): Promise<CanonicalAgentCallDetailResponse | null> {
  const identity = parseCanonicalCardIdentity(messageId)
  if (!identity) return null
  return apiClient<CanonicalAgentCallDetailResponse>(getApiUrl(
    `rooms/${encodeURIComponent(roomId)}/agent-calls/${encodeURIComponent(identity.runId)}/${encodeURIComponent(identity.publicCallId)}/detail`,
  ), { getToken, signal })
}
