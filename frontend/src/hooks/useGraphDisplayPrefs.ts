'use client'

import { useCallback, useState } from 'react'
import { DEFAULT_GRAPH_DISPLAY, type GraphDisplayPrefs } from '@/lib/graph-labels'

export const GRAPH_DISPLAY_STORAGE_KEY = 'hybro.network.graph-display'

function read(): GraphDisplayPrefs {
  if (typeof window === 'undefined') return DEFAULT_GRAPH_DISPLAY
  try {
    const value: unknown = JSON.parse(localStorage.getItem(GRAPH_DISPLAY_STORAGE_KEY) ?? 'null')
    if (value && typeof value === 'object') {
      const prefs = value as Partial<GraphDisplayPrefs>
      return {
        alwaysSubnetLabels: typeof prefs.alwaysSubnetLabels === 'boolean' ? prefs.alwaysSubnetLabels : DEFAULT_GRAPH_DISPLAY.alwaysSubnetLabels,
        alwaysAgentLabels: typeof prefs.alwaysAgentLabels === 'boolean' ? prefs.alwaysAgentLabels : DEFAULT_GRAPH_DISPLAY.alwaysAgentLabels,
      }
    }
  } catch {
    // Corrupt storage falls back to defaults.
  }
  return DEFAULT_GRAPH_DISPLAY
}

export function useGraphDisplayPrefs(): [GraphDisplayPrefs, (next: GraphDisplayPrefs) => void] {
  // The workspace renders under a client-only Suspense boundary, so reading storage during init is safe.
  const [prefs, setPrefs] = useState(read)
  const save = useCallback((next: GraphDisplayPrefs) => {
    setPrefs(next)
    try {
      localStorage.setItem(GRAPH_DISPLAY_STORAGE_KEY, JSON.stringify(next))
    } catch {
      // Storage may be unavailable (private mode); the choice still applies for this visit.
    }
  }, [])
  return [prefs, save]
}
