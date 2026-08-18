import { describe, expect, it } from 'vitest'
import {
  createHitlControllerState,
  hitlInteractionReducer,
} from '@/lib/hitl/interaction-controller'

const seed = {
  interactionKey: 'interaction-1:request-1,request-2',
  firstRequestId: 'request-1',
  answers: [
    { requestId: 'request-1' },
    { requestId: 'request-2', answer: 'saved' },
  ],
}

describe('hitlInteractionReducer', () => {
  it('owns request-keyed drafts, navigation, and review state', () => {
    let state = createHitlControllerState(seed)
    state = hitlInteractionReducer(state, {
      type: 'answer',
      requestId: 'request-1',
      value: ['a', 'b'],
    })
    state = hitlInteractionReducer(state, {
      type: 'navigate',
      requestId: 'request-2',
    })
    state = hitlInteractionReducer(state, { type: 'review' })

    expect(state.drafts).toEqual({ 'request-1': ['a', 'b'], 'request-2': 'saved' })
    expect(state.currentId).toBe('request-2')
    expect(state.reviewing).toBe(true)
  })

  it('models submit, uncertainty, and authoritative reset explicitly', () => {
    let state = createHitlControllerState(seed)
    state = hitlInteractionReducer(state, { type: 'submit_started' })
    expect(state.submission).toBe('submitting')

    state = hitlInteractionReducer(state, {
      type: 'submit_failed',
      lifecycle: 'delivery_uncertain',
      message: 'Receipt is uncertain.',
    })
    expect(state.submission).toBe('idle')
    expect(state.errorState).toBe('delivery_uncertain')

    state = hitlInteractionReducer(state, {
      type: 'reset',
      seed: {
        interactionKey: 'interaction-2:request-3',
        firstRequestId: 'request-3',
        answers: [],
      },
    })
    expect(state.interactionKey).toBe('interaction-2:request-3')
    expect(state.errorState).toBeNull()
    expect(state.drafts).toEqual({})
  })

  it('keeps a terminal server state dominant over optimistic submission', () => {
    let state = createHitlControllerState(seed)
    state = hitlInteractionReducer(state, { type: 'submit_started' })
    state = hitlInteractionReducer(state, {
      type: 'server_reconciled',
      lifecycle: 'expired',
      message: 'Expired on the server.',
    })

    expect(state.errorState).toBe('expired')
    expect(state.errorMessage).toBe('Expired on the server.')
  })
})
