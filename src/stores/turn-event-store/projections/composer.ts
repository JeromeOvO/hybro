import type {
  TurnEvent,
  ComposerStateView,
  HitlPromptView,
  ProjectionReducer,
} from '../types'

function sortPendingHitls(hitls: HitlPromptView[]): HitlPromptView[] {
  return [...hitls].sort((a, b) => {
    // Grouped items first
    const aGrouped = a.groupId !== undefined
    const bGrouped = b.groupId !== undefined

    if (aGrouped && !bGrouped) return -1
    if (!aGrouped && bGrouped) return 1

    // Both grouped: sort by groupId, then groupIndex
    if (aGrouped && bGrouped) {
      if (a.groupId !== b.groupId) {
        return a.groupId!.localeCompare(b.groupId!)
      }
      return (a.groupIndex ?? 0) - (b.groupIndex ?? 0)
    }

    // Both ungrouped: sort by ts
    return a.ts - b.ts
  })
}

export const composerReducer: ProjectionReducer<ComposerStateView> = {
  init(): ComposerStateView {
    return {
      mode: 'normal',
      pendingHitls: [],
      isProcessing: false,
    }
  },

  reduce(view: ComposerStateView, event: TurnEvent): ComposerStateView {
    switch (event.type) {
      case 'turn_started':
        return { ...view, isProcessing: true }

      case 'turn_completed':
      case 'turn_failed':
      case 'turn_canceled':
        return { ...view, isProcessing: false }

      case 'hitl_requested': {
        // Deduplicate by hitlId — multiple injection paths (hydration +
        // overlay) may emit the same pending request with different eventIds.
        if (view.pendingHitls.some(h => h.hitlId === event.hitlId)) {
          return view
        }
        const newHitl: HitlPromptView = {
          hitlId: event.hitlId,
          turnId: event.turnId,
          ts: event.ts,
          source: event.source,
          agentName: event.agentName,
          prompt: event.prompt,
          promptType: event.promptType,
          choices: event.choices,
          groupId: event.groupId,
          groupTotal: event.groupTotal,
          groupIndex: event.groupIndex,
        }
        const pending = sortPendingHitls([...view.pendingHitls, newHitl])
        return {
          ...view,
          mode: 'hitl_responding',
          pendingHitls: pending,
        }
      }

      case 'hitl_answered':
      case 'hitl_expired':
      case 'hitl_canceled':
      case 'hitl_error': {
        const pending = view.pendingHitls.filter(h => h.hitlId !== event.hitlId)
        return {
          ...view,
          mode: pending.length > 0 ? 'hitl_responding' : 'normal',
          pendingHitls: pending,
        }
      }

      default:
        return view
    }
  },
}
