import type { ApiError } from '@/lib/api-client'
import type { CanonicalAgentCallDetailResponse } from '@/lib/api/agent-call-detail'
import {
  fetchCanonicalAgentCallDetail,
  parseCanonicalCardIdentity,
} from '@/lib/api/agent-call-detail'

const RETRY_DELAYS_MS = [100, 300] as const

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= RETRY_DELAYS_MS.length) return false
  const status = (error as ApiError | undefined)?.status
  return status == null || status === 404 || status === 503
}

export function canonicalAgentCallDetailQueryOptions(
  roomId: string,
  messageId: string | undefined,
  getToken: (() => Promise<string | null>) | undefined,
  enabled: boolean,
) {
  const identity = messageId ? parseCanonicalCardIdentity(messageId) : null
  return {
    queryKey: [
      'canonical-agent-call-detail',
      roomId,
      identity?.runId ?? '',
      identity?.publicCallId ?? '',
    ] as const,
    queryFn: async ({ signal }: { signal: AbortSignal }): Promise<CanonicalAgentCallDetailResponse> => {
      if (!messageId || !identity) throw new Error('Canonical Agent call identity is unavailable')
      const detail = await fetchCanonicalAgentCallDetail(
        roomId,
        messageId,
        getToken,
        signal,
      )
      if (!detail) throw new Error('Canonical Agent call detail is unavailable')
      return detail
    },
    enabled: enabled && identity != null,
    staleTime: Number.POSITIVE_INFINITY,
    retry: shouldRetry,
    retryDelay: (attempt: number) => RETRY_DELAYS_MS[attempt] ?? RETRY_DELAYS_MS.at(-1)!,
  }
}
