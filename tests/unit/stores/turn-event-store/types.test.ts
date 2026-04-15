import { describe, it, expect } from 'vitest'
import type {
  TurnEvent,
  TurnEventEnvelope,
  ContentSlotView,
  RailItemView,
  ComposerStateView,
  HitlPromptView,
  PhasePayload,
  UserInputData,
  ArtifactData,
} from '@/stores/turn-event-store/types'
import {
  isTurnTerminal,
  isSlotTerminal,
  TURN_TERMINAL_TYPES,
  SLOT_TERMINAL_STATUSES,
} from '@/stores/turn-event-store/types'

describe('TurnEvent type guards', () => {
  it('isTurnTerminal returns true for terminal types', () => {
    expect(isTurnTerminal('turn_completed')).toBe(true)
    expect(isTurnTerminal('turn_failed')).toBe(true)
    expect(isTurnTerminal('turn_canceled')).toBe(true)
  })

  it('isTurnTerminal returns false for non-terminal types', () => {
    expect(isTurnTerminal('turn_started')).toBe(false)
    expect(isTurnTerminal('phase_changed')).toBe(false)
    expect(isTurnTerminal('slot_opened')).toBe(false)
  })

  it('isSlotTerminal returns true for terminal statuses', () => {
    expect(isSlotTerminal('completed')).toBe(true)
    expect(isSlotTerminal('failed')).toBe(true)
    expect(isSlotTerminal('canceled')).toBe(true)
    expect(isSlotTerminal('rejected')).toBe(true)
  })

  it('isSlotTerminal returns false for streaming', () => {
    expect(isSlotTerminal('streaming')).toBe(false)
  })

  it('TURN_TERMINAL_TYPES has all 3 terminal types', () => {
    expect(TURN_TERMINAL_TYPES).toEqual(['turn_completed', 'turn_failed', 'turn_canceled'])
  })
})
