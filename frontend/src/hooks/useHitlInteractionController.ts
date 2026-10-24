'use client'

import { useEffect, useReducer } from 'react'
import {
  createHitlControllerState,
  forgetHitlDrafts,
  hitlInteractionReducer,
  rememberHitlDrafts,
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

  const draftKey = seed.draftKey
  useEffect(() => {
    if (!draftKey || state.interactionKey !== seed.interactionKey) return
    if (state.submission === 'submitted') forgetHitlDrafts(draftKey)
    else rememberHitlDrafts(draftKey, state.drafts)
  }, [draftKey, seed.interactionKey, state.drafts, state.interactionKey, state.submission])

  return { state, dispatch }
}
