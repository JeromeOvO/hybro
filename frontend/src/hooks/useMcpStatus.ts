'use client'

import { useQuery } from '@tanstack/react-query'
import { parseMcpStatus, type McpStatus } from '@/lib/mcp'

export type McpCheck = McpStatus | 'checking' | 'error'

const STATUS_TIMEOUT_MS = 6000

export function useMcpStatus(): { status: McpCheck; refresh: () => void } {
  const query = useQuery({
    queryKey: ['hybro-mcp', 'status'],
    retry: false,
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      // Abort on unmount (query signal) or after the timeout, whichever comes first.
      const controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS)
      const onAbort = () => controller.abort()
      signal.addEventListener('abort', onAbort, { once: true })
      try {
        const response = await fetch('/hybro-mcp', { cache: 'no-store', signal: controller.signal })
        if (!response.ok) throw new Error('Status unavailable')
        return parseMcpStatus(await response.json())
      } finally {
        clearTimeout(timeout)
        signal.removeEventListener('abort', onAbort)
      }
    },
  })
  const status: McpCheck = query.isFetching && !query.data ? 'checking' : query.isError ? 'error' : query.data ?? 'checking'
  return { status, refresh: () => { void query.refetch() } }
}
