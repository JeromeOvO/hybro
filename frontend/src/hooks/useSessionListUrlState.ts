'use client'

import { useCallback, useEffect, useMemo, useRef } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import {
  parseSessionListState,
  serializeSessionListState,
  type SessionListState,
} from '@/lib/session-list-state'

export function useSessionListUrlState(): [SessionListState, (patch: Partial<SessionListState>) => void, string] {
  const searchParams = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const query = searchParams.toString()
  const state = useMemo(() => parseSessionListState(new URLSearchParams(query)), [query])

  // The router applies a replace asynchronously. Two quick changes (a filter,
  // then typing) must build on the last requested URL, not on the URL of the
  // previous render, or the second change drops the first.
  const requested = useRef<string | null>(null)
  useEffect(() => {
    if (requested.current === query) requested.current = null
  }, [query])

  const update = useCallback((patch: Partial<SessionListState>) => {
    const base = parseSessionListState(new URLSearchParams(requested.current ?? query))
    const next = serializeSessionListState({ ...base, ...patch })
    requested.current = next
    // Filters replace the history entry, so Back leaves the list instead of
    // stepping through every filter change.
    router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false })
  }, [pathname, query, router])

  return [state, update, query]
}
