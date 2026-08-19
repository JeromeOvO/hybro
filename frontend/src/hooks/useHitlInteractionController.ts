'use client'

import { useEffect, useReducer } from 'react'
import {
  createHitlControllerState,
  hitlInteractionReducer,
  type HitlControllerSeed,
} from '@/lib/hitl/interaction-controller'

export function useHitlInteractionController(seed: HitlControllerSeed) {
  const [state, dispatch] = useReducer(
    hitlInteractionReducer,
    seed,
    createHitlControllerState,
  )

  useEffect(() => {
    if (state.interactionKey !== seed.interactionKey) {
      dispatch({ type: 'reset', seed })
    }
  }, [seed, state.interactionKey])

  return { state, dispatch }
}
