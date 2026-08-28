import { describe, expect, it } from 'vitest'
import {
  DEFAULT_CHAT_MODE,
  chatModeToExecutionMode,
  roomDefaultToChatMode,
  roomUsesSupervisorByDefault,
} from '@/lib/types/chat-mode'

describe('chat-mode helpers', () => {
  it('maps Ultimate to request-scoped supervisor mode', () => {
    expect(chatModeToExecutionMode('ultimate')).toBe('supervisor')
  })

  it('maps Fast to request-scoped direct mode', () => {
    expect(chatModeToExecutionMode('fast')).toBe('direct')
  })

  it('uses the room supervisor flag only as the UI default', () => {
    expect(roomUsesSupervisorByDefault(true)).toBe(true)
    expect(roomUsesSupervisorByDefault(false)).toBe(false)
    expect(roomUsesSupervisorByDefault()).toBe(true)
    expect(roomDefaultToChatMode(true)).toBe('ultimate')
    expect(roomDefaultToChatMode(false)).toBe('fast')
  })

  it('defaults missing room metadata to Ultimate', () => {
    expect(DEFAULT_CHAT_MODE).toBe('ultimate')
    expect(roomDefaultToChatMode()).toBe('ultimate')
  })
})
