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

/** An explicit false is the only room-level override of the product default. */
export function roomUsesSupervisorByDefault(useSupervisor?: boolean): boolean {
  return useSupervisor !== false
}

/** Legacy room flags are UI defaults only; an absent flag uses the product default. */
export function roomDefaultToChatMode(useSupervisor?: boolean): ChatMode {
  return roomUsesSupervisorByDefault(useSupervisor)
    ? DEFAULT_CHAT_MODE
    : CHAT_MODE.FAST
}
