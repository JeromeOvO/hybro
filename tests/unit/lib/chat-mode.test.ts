import { describe, it, expect } from 'vitest'
import {
  CHAT_MODE,
  DEFAULT_CHAT_MODE,
  chatModeToSupervisor,
  supervisorToChatMode,
} from '@/lib/types/chat-mode'

describe('chat-mode helpers', () => {
  it('chatModeToSupervisor returns true for ultimate', () => {
    expect(chatModeToSupervisor('ultimate')).toBe(true)
  })

  it('chatModeToSupervisor returns false for fast', () => {
    expect(chatModeToSupervisor('fast')).toBe(false)
  })

  it('supervisorToChatMode returns ultimate for true', () => {
    expect(supervisorToChatMode(true)).toBe('ultimate')
  })

  it('supervisorToChatMode returns fast for false', () => {
    expect(supervisorToChatMode(false)).toBe('fast')
  })

  it('DEFAULT_CHAT_MODE is ultimate', () => {
    expect(DEFAULT_CHAT_MODE).toBe(CHAT_MODE.ULTIMATE)
  })

  it('round-trips correctly', () => {
    expect(supervisorToChatMode(chatModeToSupervisor('ultimate'))).toBe('ultimate')
    expect(supervisorToChatMode(chatModeToSupervisor('fast'))).toBe('fast')
  })
})
