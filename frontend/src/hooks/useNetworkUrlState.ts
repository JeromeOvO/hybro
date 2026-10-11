'use client'

import { useCallback, useMemo } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { parseNetworkUrlState, serializeNetworkUrlState, type NetworkUrlState } from '@/lib/network-url-state'

export function useNetworkUrlState(): [NetworkUrlState, (patch: Partial<NetworkUrlState>) => void] {
  const searchParams = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const query = searchParams.toString()
  const state = useMemo(() => parseNetworkUrlState(new URLSearchParams(query)), [query])

  const update = useCallback((patch: Partial<NetworkUrlState>) => {
    const next = serializeNetworkUrlState({ ...state, ...patch })
    const href = next ? `${pathname}?${next}` : pathname
    // Search text replaces the entry so typing does not flood history.
    const onlySearch = Object.keys(patch).every(key => key === 'q')
    if (onlySearch) router.replace(href, { scroll: false })
    else router.push(href, { scroll: false })
  }, [pathname, router, state])

  return [state, update]
}
