export type ChatMode = 'ultimate' | 'fast' | 'ultimate_debate' | 'fast_debate'

export const CHAT_MODE = {
  ULTIMATE: 'ultimate' as const,
  FAST: 'fast' as const,
  ULTIMATE_DEBATE: 'ultimate_debate' as const,
  FAST_DEBATE: 'fast_debate' as const,
}

export const DEFAULT_CHAT_MODE: ChatMode = CHAT_MODE.ULTIMATE

/** Convert ChatMode to the backend flags (use_supervisor + debateMode) */
export function chatModeToFlags(mode: ChatMode): { use_supervisor: boolean; debateMode: boolean } {
  switch (mode) {
    case CHAT_MODE.ULTIMATE: return { use_supervisor: true, debateMode: false }
    case CHAT_MODE.FAST: return { use_supervisor: false, debateMode: false }
    case CHAT_MODE.ULTIMATE_DEBATE: return { use_supervisor: true, debateMode: true }
    case CHAT_MODE.FAST_DEBATE: return { use_supervisor: false, debateMode: true }
  }
}

/** Convert the backend flags into a ChatMode */
export function flagsToChatMode(useSupervisor: boolean, debateMode: boolean): ChatMode {
  if (debateMode) {
    return useSupervisor ? CHAT_MODE.ULTIMATE_DEBATE : CHAT_MODE.FAST_DEBATE
  }
  return useSupervisor ? CHAT_MODE.ULTIMATE : CHAT_MODE.FAST
}
