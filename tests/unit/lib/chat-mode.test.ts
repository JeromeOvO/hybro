import { describe, it, expect } from 'vitest'
import {
  CHAT_MODE,
  DEFAULT_CHAT_MODE,
  chatModeToFlags,
  flagsToChatMode,
} from '@/lib/types/chat-mode'

describe('chat-mode helpers', () => {
  describe('chatModeToFlags', () => {
    it('ultimate → supervisor on, debate off', () => {
      expect(chatModeToFlags('ultimate')).toEqual({ use_supervisor: true, debateMode: false })
    })

    it('fast → supervisor off, debate off', () => {
      expect(chatModeToFlags('fast')).toEqual({ use_supervisor: false, debateMode: false })
    })

    it('ultimate_debate → supervisor on, debate on', () => {
      expect(chatModeToFlags('ultimate_debate')).toEqual({ use_supervisor: true, debateMode: true })
    })

    it('fast_debate → supervisor off, debate on', () => {
      expect(chatModeToFlags('fast_debate')).toEqual({ use_supervisor: false, debateMode: true })
    })
  })

  describe('flagsToChatMode', () => {
    it('supervisor on, debate off → ultimate', () => {
      expect(flagsToChatMode(true, false)).toBe('ultimate')
    })

    it('supervisor off, debate off → fast', () => {
      expect(flagsToChatMode(false, false)).toBe('fast')
    })

    it('supervisor on, debate on → ultimate_debate', () => {
      expect(flagsToChatMode(true, true)).toBe('ultimate_debate')
    })

    it('supervisor off, debate on → fast_debate', () => {
      expect(flagsToChatMode(false, true)).toBe('fast_debate')
    })
  })

  describe('round-trips', () => {
    for (const mode of ['ultimate', 'fast', 'ultimate_debate', 'fast_debate'] as const) {
      it(`round-trips ${mode}`, () => {
        const flags = chatModeToFlags(mode)
        expect(flagsToChatMode(flags.use_supervisor, flags.debateMode)).toBe(mode)
      })
    }
  })

  it('DEFAULT_CHAT_MODE is ultimate', () => {
    expect(DEFAULT_CHAT_MODE).toBe(CHAT_MODE.ULTIMATE)
  })
})
