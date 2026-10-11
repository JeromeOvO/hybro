import { afterEach, describe, expect, it } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { GRAPH_DISPLAY_STORAGE_KEY, useGraphDisplayPrefs } from '@/hooks/useGraphDisplayPrefs'

afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe('useGraphDisplayPrefs', () => {
  it('loads, saves, and ignores corrupt storage', () => {
    localStorage.setItem(GRAPH_DISPLAY_STORAGE_KEY, '{not json')
    const { result } = renderHook(() => useGraphDisplayPrefs())
    expect(result.current[0]).toEqual({ alwaysSubnetLabels: true, alwaysAgentLabels: false })
    act(() => result.current[1]({ alwaysSubnetLabels: false, alwaysAgentLabels: true }))
    expect(JSON.parse(localStorage.getItem(GRAPH_DISPLAY_STORAGE_KEY)!)).toEqual({ alwaysSubnetLabels: false, alwaysAgentLabels: true })
    const reloaded = renderHook(() => useGraphDisplayPrefs())
    expect(reloaded.result.current[0]).toEqual({ alwaysSubnetLabels: false, alwaysAgentLabels: true })
  })
})
