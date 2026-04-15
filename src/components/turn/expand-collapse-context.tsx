'use client'

import { createContext, useContext } from 'react'

interface ExpandCollapseSignals {
  expandSignal: number
  collapseSignal: number
}

export const ExpandCollapseContext = createContext<ExpandCollapseSignals>({
  expandSignal: 0,
  collapseSignal: 0,
})

export function useExpandCollapseSignals() {
  return useContext(ExpandCollapseContext)
}
