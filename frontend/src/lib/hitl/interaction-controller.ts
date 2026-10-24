import type { HitlLifecycleState } from '@/lib/selectors/conversation-types'

export type HitlDraftValue = string | string[]
export type HitlDrafts = Record<string, HitlDraftValue>

export type HitlControllerState = {
  interactionKey: string
  currentId: string | null
  drafts: HitlDrafts
  submission: 'idle' | 'submitting' | 'submitted'
  errorState: HitlLifecycleState | null
  errorMessage: string | null
}

export type HitlControllerSeed = {
  interactionKey: string
  /** Identity of the exact question set; a new revision gets a new key. */
  draftKey?: string | null
  firstRequestId: string | null
  answers: Array<{ requestId: string; answer?: string }>
}

export type HitlControllerAction =
  | { type: 'reset'; seed: HitlControllerSeed }
  | { type: 'answer'; requestId: string; value: HitlDraftValue }
  | { type: 'navigate'; requestId: string }
  | { type: 'submit_started' }
  | { type: 'submit_succeeded' }
  | { type: 'submit_failed'; message: string; lifecycle?: HitlLifecycleState }
  | { type: 'server_reconciled'; lifecycle: HitlLifecycleState; message?: string }

// Unsent drafts per question set (interaction id plus its questions). The response bar remounts whenever its
// interaction key or the composer mode changes (SSE reconnects, snapshot
// refreshes); without this the user's typing is lost on every remount.
const unsentDrafts = new Map<string, HitlDrafts>()
const MAX_UNSENT_INTERACTIONS = 20

export function rememberHitlDrafts(draftKey: string, drafts: HitlDrafts): void {
  unsentDrafts.delete(draftKey)
  if (Object.keys(drafts).length === 0) return
  unsentDrafts.set(draftKey, drafts)
  const oldest = unsentDrafts.keys().next().value
  if (unsentDrafts.size > MAX_UNSENT_INTERACTIONS && oldest !== undefined) {
    unsentDrafts.delete(oldest)
  }
}

export function forgetHitlDrafts(draftKey: string): void {
  unsentDrafts.delete(draftKey)
}

/** Test isolation only. */
export function resetUnsentHitlDrafts(): void {
  unsentDrafts.clear()
}

export function createHitlControllerState(seed: HitlControllerSeed): HitlControllerState {
  return {
    interactionKey: seed.interactionKey,
    currentId: seed.firstRequestId,
    drafts: {
      ...(seed.draftKey ? unsentDrafts.get(seed.draftKey) : undefined),
      // Answers the server already holds win over a local draft.
      ...Object.fromEntries(
        seed.answers
          .filter(item => item.answer)
          .map(item => [item.requestId, item.answer as string]),
      ),
    },
    submission: 'idle',
    errorState: null,
    errorMessage: null,
  }
}

export function hitlInteractionReducer(
  state: HitlControllerState,
  action: HitlControllerAction,
): HitlControllerState {
  switch (action.type) {
    case 'reset':
      return createHitlControllerState(action.seed)
    case 'answer':
      return {
        ...state,
        drafts: { ...state.drafts, [action.requestId]: action.value },
        errorMessage: null,
      }
    case 'navigate':
      return { ...state, currentId: action.requestId }
    case 'submit_started':
      if (state.submission === 'submitting') return state
      return { ...state, submission: 'submitting', errorMessage: null }
    case 'submit_succeeded':
      return { ...state, submission: 'submitted', errorMessage: null }
    case 'submit_failed':
      return {
        ...state,
        submission: 'idle',
        errorState: action.lifecycle ?? state.errorState,
        errorMessage: action.message,
      }
    case 'server_reconciled':
      return {
        ...state,
        errorState: action.lifecycle === 'open' ? null : action.lifecycle,
        errorMessage: action.message ?? state.errorMessage,
        submission: action.lifecycle === 'applying' ? 'submitted' : 'idle',
      }
  }
}
