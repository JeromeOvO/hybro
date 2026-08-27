import { apiClient } from '@/lib/api-client'
import { getApiUrl } from '@/lib/utils'
import type { ArtifactData } from '@/stores/message-store/types'

export interface CanonicalArtifactDescriptor {
  artifact_ref: string
  file_id?: string | null
  name?: string | null
  mime_type?: string | null
  size_bytes?: number | null
}

export interface CanonicalAgentCallDetailResponse {
  run_id: string
  public_call_id: string
  status: string
  output: string
  artifacts: CanonicalArtifactDescriptor[]
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
