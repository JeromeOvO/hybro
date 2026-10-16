'use client'

import { useQuery } from '@tanstack/react-query'
import { listSessions, type SessionListResponse, type SessionSummary } from '@/lib/api/session'

// Sessions are opened by clients outside the app, so the list polls even when
// nothing in it is active: a new session waiting for approval must appear
// without a remount.
export const SESSIONS_POLL_MS = 15_000

export const sessionsQueryKey = (userId: string) => ['sessions', userId] as const

export function sessionsQueryOptions(userId: string) {
  return {
    queryKey: sessionsQueryKey(userId),
    queryFn: ({ signal }: { signal: AbortSignal }) => listSessions(undefined, signal),
    staleTime: 10_000,
    refetchInterval: SESSIONS_POLL_MS,
    refetchOnWindowFocus: true,
  }
}

export function useSessions({ userId, enabled }: { userId: string; enabled: boolean }) {
  return useQuery<SessionListResponse>({ ...sessionsQueryOptions(userId), enabled })
}

export function waitingOnOwnerCount(sessions: readonly SessionSummary[] | undefined): number {
  return sessions?.filter(session => session.state === 'waiting_on_owner').length ?? 0
}
