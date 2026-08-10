import type { ExecutionMode } from './request'

export type ChatMode = 'ultimate' | 'fast'

export const CHAT_MODE = {
  ULTIMATE: 'ultimate' as const,
  FAST: 'fast' as const,
}

export const DEFAULT_CHAT_MODE: ChatMode = CHAT_MODE.ULTIMATE

export function chatModeToExecutionMode(mode: ChatMode): ExecutionMode {
  return mode === CHAT_MODE.ULTIMATE ? 'supervisor' : 'direct'
}

/** Legacy room flags are UI defaults only; debateMode is intentionally ignored. */
export function roomDefaultToChatMode(useSupervisor: boolean): ChatMode {
  return useSupervisor ? CHAT_MODE.ULTIMATE : CHAT_MODE.FAST
}
