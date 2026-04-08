export type ChatMode = 'ultimate' | 'fast'

export const CHAT_MODE = {
  ULTIMATE: 'ultimate' as const,
  FAST: 'fast' as const,
}

export const DEFAULT_CHAT_MODE: ChatMode = CHAT_MODE.ULTIMATE

/** Convert ChatMode to the boolean backend expects on extend_info.use_supervisor */
export function chatModeToSupervisor(mode: ChatMode): boolean {
  return mode === CHAT_MODE.ULTIMATE
}

/** Convert the backend boolean into a ChatMode */
export function supervisorToChatMode(useSupervisor: boolean): ChatMode {
  return useSupervisor ? CHAT_MODE.ULTIMATE : CHAT_MODE.FAST
}
